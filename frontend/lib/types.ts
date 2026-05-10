/**
 * TypeScript types for the Universal Chat JSON (UCJ) schema and the
 * dashboard's view-model.
 *
 * These MUST stay in sync with `backend/app/schemas/ucj.py` and
 * `backend/app/schemas/upload.py`. UCJ is the canonical wire format -
 * frontend never invents new fields, backend is source of truth for additions.
 */

// ---- UCJ Core --------------------------------------------------------------

export type MessageType =
  | "text"
  | "image"
  | "video"
  | "audio"
  | "sticker"
  | "file"
  | "deleted"
  | "system";

export interface MessageMetadata {
  word_count: number;
  char_count: number;
  has_emoji: boolean;
  emojis: string[];
  has_url: boolean;
  is_deleted: boolean;
  has_media: boolean;
}

export interface Message {
  id: string;
  sender: string;
  /** ISO 8601 timestamp string. Convert with new Date(msg.timestamp). */
  timestamp: string;
  content: string;
  type: MessageType;
  /** Source-platform id of the message this one replies to, if any. */
  reply_to_id: string | null;
  metadata: MessageMetadata;
}

export interface DateRange {
  /** ISO 8601 */
  start: string;
  /** ISO 8601 */
  end: string;
  span_days: number;
}

export interface ChatStats {
  total_words: number;
  total_chars: number;
  total_emojis: number;
  total_media: number;
  /** Map of sender name -> message count. */
  messages_per_sender: Record<string, number>;
  avg_message_length: number;
  /** Per-sender breakdown surfaced by UCJBuilder (extra="allow" passes it through). */
  per_sender_breakdown?: Record<
    string,
    { messages: number; words: number; chars: number }
  >;
  /** Platforms may add extra fields - allow them through. */
  [extra: string]: unknown;
}

export interface AIAnalysis {
  summary: string | null;
  topics: string[];
  /** Overall sentiment in [-1, 1]. */
  sentiment_overall: number | null;
  /** Per-sender sentiment in [-1, 1]. */
  sentiment_per_sender: Record<string, number>;
  relationship_dynamic: string | null;
  /** ISO 8601 */
  generated_at: string | null;
  model: string | null;
  /** Optional Claude-generated insights from the converter step. */
  conversation_tone?: string;
  relationship_type?: "romantic" | "friendship" | "family" | "professional" | "group";
  language_style?: "formal" | "casual" | "very_casual" | "mixed";
  dominant_themes?: string[];
  most_active_sender?: string;
  notable_patterns?: string;
  data_quality?: "good" | "fair" | "poor";
  parsing_notes?: string;
  [extra: string]: unknown;
}

export interface ChatMeta {
  source_platform: string;
  source_file: string;
  /** ISO 8601 */
  exported_at: string;
  participants: string[];
  total_messages: number;
  date_range: DateRange;
  stats: ChatStats;
  ai_analysis?: AIAnalysis | null;
}

export interface UCJFile {
  ucj_version: string;
  meta: ChatMeta;
  messages: Message[];
}

// ---- Platform detection (matches backend `PlatformType` enum) -------------

export type Platform =
  | "whatsapp"
  | "telegram"
  | "instagram"
  | "facebook"
  | "csv"
  | "unknown";

/** Visual identity per platform - mirrors the JSX converter's badge palette. */
export const PLATFORM_INFO: Record<
  Platform,
  { name: string; color: string; icon: string }
> = {
  whatsapp: { name: "WhatsApp", color: "#25D366", icon: "whatsapp" },
  telegram: { name: "Telegram", color: "#2AABEE", icon: "telegram" },
  instagram: { name: "Instagram", color: "#E1306C", icon: "instagram" },
  facebook: { name: "Facebook Messenger", color: "#0084FF", icon: "facebook" },
  csv: { name: "CSV", color: "#F59E0B", icon: "csv" },
  unknown: { name: "Unknown", color: "#6B7280", icon: "file" },
};

// ---- Upload flow (matches `app/schemas/upload.py`) -------------------------

/**
 * Stages an upload progresses through. Backend emits these via WebSocket and
 * the GET /upload/{id} status endpoint. Used to drive the multi-stage UI.
 */
export type ProcessingStage =
  | "queued"
  | "parsing"
  | "persisting"
  | "ready"
  | "failed";

/** UI-only stage label - layered on top of ProcessingStage to surface client-side AI work. */
export type ClientStage = ProcessingStage | "ai_enhancing" | "idle" | "detecting";

export interface UploadResponse {
  upload_id: string;
  filename: string;
  detected_platform: Platform | string;
  detection_confidence: number;
  detection_reason: string;
  status: ProcessingStage;
  meta: ChatMeta | null;
  skipped_count: number;
}

export interface UploadStatus {
  upload_id: string;
  status: ProcessingStage;
  progress: number;
  stage_detail: string;
  error: string | null;
  updated_at: string;
}

export interface UploadProgressEvent {
  upload_id: string;
  stage: ProcessingStage;
  progress: number;
  message: string;
  timestamp: string;
}

// ---- Dashboard view-model --------------------------------------------------
// These types describe what the dashboard *renders*, not what the API stores.
// Keeping them separate lets us evolve the UI without changing the wire format.

export interface SentimentPoint {
  /** ISO 8601 - bucketed (day/hour) on the backend before sending. */
  timestamp: string;
  /** Sentiment in [-1, 1]. */
  value: number;
  /** Per-sender breakdown for stacked charts. */
  bySender?: Record<string, number>;
}

export interface ActivityPoint {
  /** Bucket label - "2025-01-14", "Mon", "14:00", etc. */
  bucket: string;
  count: number;
  bySender?: Record<string, number>;
}

export interface TopicCluster {
  topic: string;
  /** Relative weight 0-1 for sizing in word clouds / treemaps. */
  weight: number;
  exampleMessageIds: string[];
}

export interface ParticipantSummary {
  name: string;
  messageCount: number;
  wordCount: number;
  /** [-1, 1] */
  avgSentiment: number;
  topEmojis: string[];
  /** Hour buckets 0-23, value = activity count. */
  hourlyActivity: number[];
}

export interface DashboardData {
  chat: UCJFile;
  sentimentTimeline: SentimentPoint[];
  activityTimeline: ActivityPoint[];
  topics: TopicCluster[];
  participants: ParticipantSummary[];
}

// ---- Dashboard stats (matches /chats/{id}/dashboard payload) -------------

/** Top-level metrics rendered in the dashboard header strip. */
export interface DashboardStats {
  upload_id: string;
  total_messages: number;
  total_words: number;
  total_emojis: number;
  span_days: number;
  participants: string[];
  /** [-1, 1] */
  sentiment_overall: number;
  /** Pre-computed sender breakdown — same shape as ParticipantSummary. */
  participants_summary: ParticipantSummary[];
  /** Pre-bucketed timelines for the overview charts. */
  sentiment_timeline: SentimentPoint[];
  activity_timeline: ActivityPoint[];
  topics: TopicCluster[];
  /** Surfaced on the overview "highlights" panel. */
  highlights: Highlight[];
}

export type EmotionLabel =
  | "joy"
  | "sadness"
  | "anger"
  | "fear"
  | "love"
  | "surprise"
  | "disgust";

export type SentimentLabel = "positive" | "negative" | "neutral";

export interface Highlight {
  /** A short label e.g. "Most loving message", "Conflict spike". */
  label: string;
  /** ISO 8601 */
  timestamp: string;
  sender: string;
  preview: string;
  message_id: string;
  emotion?: EmotionLabel;
}

// ---- Search ---------------------------------------------------------------

export interface SearchFilters {
  sender?: string;
  date_from?: string;
  date_to?: string;
  emotion_label?: EmotionLabel;
  sentiment_label?: SentimentLabel;
  msg_type?: string;
  /** Cosine similarity floor [0, 1]. Default 0.3. */
  min_similarity?: number;
}

export interface SearchHitMessage {
  id: string;
  msg_id: string;
  sender: string;
  timestamp: string;
  content: string;
  msg_type: string;
  sentiment_label?: SentimentLabel | null;
  emotion_label?: EmotionLabel | null;
  topics?: string[] | null;
}

export interface ContextWindow {
  before: SearchHitMessage[];
  after: SearchHitMessage[];
}

export interface SearchResult {
  message: SearchHitMessage;
  similarity: number;
  context: ContextWindow | null;
}

export interface CitedMessage {
  message_id: string;
  sender: string;
  timestamp: string;
  content: string;
  similarity: number;
}

export interface SearchResponse {
  upload_id: string;
  query: string;
  rephrased_query: string;
  answer: string;
  cited_messages: CitedMessage[];
  search_method: "semantic" | "nl_qa" | "hybrid";
  confidence: number;
}

export interface QuerySuggestion {
  label: string;
  query: string;
  category: string;
}

export interface SuggestionsResponse {
  upload_id: string;
  suggestions: QuerySuggestion[];
}

// ---- Stats overview (matches backend app/schemas/stats.py) ---------------

/** One row in the participant-comparison panel. */
export interface ParticipantStats {
  name: string;
  message_count: number;
  word_count: number;
  emoji_count: number;
  avg_message_length: number;
  question_count: number;
  exclamation_count: number;
  most_used_word: string | null;
  favorite_emoji: string | null;
}

/** Lightweight reference to a single message — used for "longest message"
 * / "most replied to" / "first message ever" tiles. */
export interface MessageReference {
  id: string;
  msg_id: string;
  sender: string;
  /** ISO 8601 */
  timestamp: string;
  content_preview: string;
  char_count: number;
  /** Populated for "most replied to" only. */
  reply_count: number | null;
}

export interface WhoTextsFirstSlice {
  sender: string;
  days_started: number;
  /** [0, 1] — pre-computed share so the donut renders without re-deriving. */
  share: number;
}

export interface HourlyHistogramBucket {
  hour: number; // 0-23
  count: number;
}

export interface OverviewStats {
  upload_id: string;

  // Hero
  total_messages: number;
  total_words: number;
  total_emojis: number;
  total_characters: number;

  // Date / activity windows
  conversation_days: number;
  active_days: number;
  longest_streak: number;
  longest_silence: number;
  avg_messages_per_day: number;

  // Behavioral
  avg_response_time_minutes: number | null;
  who_texts_first: WhoTextsFirstSlice[];

  // Time patterns
  busiest_hour: number | null;
  busiest_day_of_week: number | null;
  most_active_month: string | null;
  hourly_histogram: HourlyHistogramBucket[];

  // Per-participant
  per_participant: ParticipantStats[];

  // Fun facts
  longest_message: MessageReference | null;
  most_replied_to: MessageReference | null;
  first_message: MessageReference | null;
  most_used_word_overall: string | null;
}

export interface TopItem {
  value: string;
  count: number;
}

export interface DetailedParticipantStats {
  upload_id: string;
  sender: string;

  message_count: number;
  word_count: number;
  emoji_count: number;
  avg_message_length: number;
  question_count: number;
  exclamation_count: number;

  avg_sentiment: number | null;
  sentiment_share: Record<string, number>;
  emotion_share: Record<string, number>;

  hourly_histogram: HourlyHistogramBucket[];
  top_words: TopItem[];
  top_emojis: TopItem[];

  first_message: MessageReference | null;
  last_message: MessageReference | null;
}

// ---- Emotion timeline (matches backend app/schemas/emotion.py) -----------

/** The seven emotion classes the NLP pipeline emits. Order matches the
 *  donut legend so colour mapping stays stable. */
export type EmotionClass =
  | "joy"
  | "love"
  | "sadness"
  | "anger"
  | "fear"
  | "surprise"
  | "disgust";

export type Granularity = "day" | "week" | "month";

/** Lightweight message used inside emotion-timeline payloads (samples,
 *  peaks, etc.). Always carries the analyzed sentiment + emotion since
 *  callers always want them when exploring emotional context. */
export interface SampleMessage {
  msg_id: string;
  sender: string;
  /** ISO 8601 */
  timestamp: string;
  content_preview: string;
  sentiment_score: number | null;
  sentiment_label: SentimentLabel | null;
  emotion_label: EmotionClass | null;
  emotion_score: number | null;
}

export interface SenderSentimentSlice {
  sender: string;
  avg_sentiment: number | null;
  message_count: number;
}

export interface SentimentTimelinePoint {
  /** ISO date string YYYY-MM-DD. */
  date: string;
  avg_sentiment: number | null;
  message_count: number;
  dominant_emotion: EmotionClass | null;
  per_sender: SenderSentimentSlice[];
}

export interface SentimentTimeline {
  upload_id: string;
  granularity: Granularity;
  senders: string[];
  points: SentimentTimelinePoint[];
}

export interface EmotionShare {
  emotion: EmotionClass;
  count: number;
  /** [0, 1] — already normalized so the seven shares sum to 1. */
  share: number;
  sample_messages: SampleMessage[];
}

export interface EmotionDistribution {
  upload_id: string;
  total_classified: number;
  distribution: EmotionShare[];
}

export interface ParticipantEmotionDistribution {
  sender: string;
  total_classified: number;
  distribution: EmotionShare[];
}

export interface EmotionByParticipant {
  upload_id: string;
  participants: ParticipantEmotionDistribution[];
}

export type PeakType = "peak" | "valley";

export interface EmotionalPeak {
  type: PeakType;
  /** ISO date */
  bucket_start: string;
  /** ISO date */
  bucket_end: string;
  score: number;
  message_count: number;
  dominant_emotion: EmotionClass | null;
  top_messages: SampleMessage[];
}

export interface EmotionalPeaks {
  upload_id: string;
  peaks: EmotionalPeak[];
  valleys: EmotionalPeak[];
}

export interface MoodCalendarDay {
  /** ISO date YYYY-MM-DD */
  date: string;
  avg_sentiment: number | null;
  dominant_emotion: EmotionClass | null;
  message_count: number;
}

export interface MoodCalendar {
  upload_id: string;
  days: MoodCalendarDay[];
  happiest_day: MoodCalendarDay | null;
  hardest_day: MoodCalendarDay | null;
}

// ---- Words & emojis (matches backend app/schemas/words.py) ---------------

export interface WordFrequencyItem {
  word: string;
  count: number;
  pct: number;
  /** Per-sender contribution; only senders with >0 uses are included. */
  per_sender: Record<string, number>;
}

export interface WordFrequency {
  upload_id: string;
  sender: string | null;
  exclude_stopwords: boolean;
  total_tokens: number;
  items: WordFrequencyItem[];
}

export interface EmojiFrequencyItem {
  emoji: string;
  count: number;
  pct: number;
  unicode_name: string | null;
  per_sender: Record<string, number>;
  /** Mean sentiment_score across messages containing this emoji. [-1, 1]. */
  avg_sentiment: number | null;
}

export interface UniqueEmojiUse {
  emoji: string;
  sender: string;
  count: number;
  /** [0, 1] — share of this emoji's total uses owned by `sender`. */
  share: number;
  unicode_name: string | null;
}

export interface EmojiFrequencyResponse {
  upload_id: string;
  sender: string | null;
  total_emojis: number;
  items: EmojiFrequencyItem[];
  unique_to_sender: UniqueEmojiUse[];
}

export interface BigramItem {
  phrase: string;
  count: number;
  per_sender: Record<string, number>;
}

export interface BigramsResponse {
  upload_id: string;
  sender: string | null;
  items: BigramItem[];
  inside_phrases: BigramItem[];
}

export interface DistinctiveWord {
  word: string;
  count: number;
  /** [0, 1] — 1.0 == exclusive to the sender, 0.5 == even split. */
  score: number;
}

export interface SenderVocab {
  sender: string;
  unique_count: number;
  total_words: number;
  /** Type-token ratio; higher == more vocabulary variety. */
  richness_score: number;
  distinctive_words: DistinctiveWord[];
}

export interface MessageLengthPoint {
  /** ISO date YYYY-MM-DD */
  date: string;
  per_sender: Record<string, number>;
}

export interface UniqueWordsResponse {
  upload_id: string;
  total_unique: number;
  per_sender: SenderVocab[];
  length_over_time: MessageLengthPoint[];
}

export interface WordTrendPoint {
  date: string;
  count: number;
}

export interface WordTrend {
  upload_id: string;
  word: string;
  total_uses: number;
  points: WordTrendPoint[];
}

export interface LateNightHourBucket {
  hour: number;
  count: number;
}

export interface LateNightStats {
  upload_id: string;
  total_late_night: number;
  pct_of_total: number;
  per_sender: Record<string, number>;
  by_hour: LateNightHourBucket[];
  sample_messages: SampleMessage[];
}

// ---- Search streaming + context (matches search router additions) -------

/** Compact evidence record sent in the `meta` SSE event — enough info
 *  for the frontend to render evidence cards before Claude finishes. */
export interface StreamEvidence {
  msg_id: string;
  id: string;
  sender: string;
  /** ISO 8601 */
  timestamp: string;
  content: string;
  similarity: number;
  sentiment_label: SentimentLabel | null;
  emotion_label: EmotionClass | null;
}

export interface StreamMetaEvent {
  type: "meta";
  rephrased_query: string;
  search_method: "semantic" | "nl_qa" | "hybrid";
  evidence: StreamEvidence[];
}

export interface StreamDeltaEvent {
  type: "delta";
  text: string;
}

export interface StreamDoneEvent {
  type: "done";
  answer: string;
  cited_messages: CitedMessage[];
  search_method: "semantic" | "nl_qa" | "hybrid";
  confidence: number;
}

export interface StreamErrorEvent {
  type: "error";
  message: string;
}

export type StreamEvent =
  | StreamMetaEvent
  | StreamDeltaEvent
  | StreamDoneEvent
  | StreamErrorEvent;

/** Slim message shape used by the context drawer (matches MessageRead). */
export interface ContextMessage {
  id: string;
  upload_id: string;
  msg_index: number;
  msg_id: string;
  sender: string;
  /** ISO 8601 */
  timestamp: string;
  content: string;
  msg_type: string;
  reply_to_id: string | null;
  word_count: number;
  char_count: number;
  has_emoji: boolean;
  emojis: string[];
  has_url: boolean;
  is_deleted: boolean;
  has_media: boolean;
  sentiment_score: number | null;
  sentiment_label: SentimentLabel | null;
  emotion_label: EmotionClass | null;
  emotion_score: number | null;
  topics: string[] | null;
}

export interface MessageContextResponse {
  upload_id: string;
  target: ContextMessage;
  before: ContextMessage[];
  after: ContextMessage[];
}

/** Persisted across browser sessions in localStorage. */
export interface SearchHistoryItem {
  query: string;
  mode: "ai" | "find";
  /** Unix ms */
  at: number;
  uploadId: string;
}

// ---- Conflict analysis (matches backend app/schemas/conflict.py) ---------

export type ResolutionType = "apology" | "topic_change" | "time_gap" | "unresolved";

export interface ResolutionInfo {
  type: ResolutionType;
  sender: string | null;
  message: SampleMessage | null;
  minutes_after_window: number | null;
}

export interface ConflictWindow {
  window_id: string;
  start_msg_id: string;
  end_msg_id: string;
  start_db_id: string;
  end_db_id: string;
  /** ISO 8601 */
  start_timestamp: string;
  /** ISO 8601 */
  end_timestamp: string;
  duration_minutes: number;
  duration_messages: number;
  peak_negativity_score: number;
  trigger_sender: string | null;
  trigger_message: SampleMessage | null;
  resolution: ResolutionInfo;
  top_words: string[];
  sample_messages: SampleMessage[];
  /** Sentiment scores for messages in [start-5, end+5]. Null entries
   *  mark messages without a classified score — render gaps. */
  sentiment_arc: (number | null)[];
}

export interface ConflictTheme {
  label: string;
  description: string;
  frequency: number;
  window_ids: string[];
  avg_sentiment: number | null;
  example_messages: SampleMessage[];
}

export interface ConflictWordCount {
  word: string;
  count: number;
}

export interface ConflictHourCount {
  hour: number; // 0-23
  count: number;
}

export interface ConflictDayOfWeekCount {
  /** 0 == Monday … 6 == Sunday (ISO weekday minus 1). */
  dow: number;
  count: number;
}

export interface ConflictLanguage {
  conflict_words: ConflictWordCount[];
  resolution_words: ConflictWordCount[];
  hour_distribution: ConflictHourCount[];
  dow_distribution: ConflictDayOfWeekCount[];
}

export interface SenderRole {
  sender: string;
  count: number;
  total: number;
}

export interface ConflictMonthCount {
  month: string; // ISO YYYY-MM
  count: number;
}

export interface ConflictSummary {
  total_conflicts_detected: number;
  avg_duration_hours: number;
  avg_recovery_time_hours: number | null;
  most_common_triggers: string[];
  who_escalates_more: SenderRole | null;
  who_resolves_more: SenderRole | null;
  conflict_frequency_by_month: ConflictMonthCount[];
}

export interface ConflictAnalysisResponse {
  upload_id: string;
  summary: ConflictSummary;
  windows: ConflictWindow[];
  language: ConflictLanguage;
  themes: ConflictTheme[] | null;
}

export interface ConflictThemesResponse {
  upload_id: string;
  themes: ConflictTheme[];
  used_llm: boolean;
}

// ---- Love languages (matches backend app/schemas/love_language.py) -------

export type LoveLanguageCategory =
  | "words_of_affirmation"
  | "acts_of_service"
  | "quality_time"
  | "physical_touch"
  | "gift_giving";

export interface LoveLanguageCount {
  category: LoveLanguageCategory;
  count: number;
  share: number;
  examples: SampleMessage[];
}

export interface LoveLanguageBreakdown {
  sender: string;
  total_classified: number;
  distribution: LoveLanguageCount[];
  primary: LoveLanguageCategory | null;
  secondary: LoveLanguageCategory | null;
  summary: string;
}

export interface LoveLanguageReport {
  upload_id: string;
  participants: LoveLanguageBreakdown[];
  compatibility_insight: string;
  used_llm: boolean;
}

// ---- Health score (matches backend app/schemas/health_score.py) ---------

export type HealthFactorKey =
  | "communication_balance"
  | "response_consistency"
  | "sentiment_trend"
  | "conflict_recovery"
  | "affection_frequency"
  | "engagement_depth"
  | "shared_activities";

export type HealthScoreBand = "red" | "amber" | "green";

export interface HealthScoreFactor {
  key: HealthFactorKey;
  label: string;
  score: number;
  weight: number;
  weighted_score: number;
  raw_value: string;
  insight: string;
}

export interface HealthScoreReport {
  upload_id: string;
  overall_score: number;
  band: HealthScoreBand;
  factors: HealthScoreFactor[];
  narrative: string;
  methodology: string;
  disclaimer: string;
  used_llm: boolean;
}
