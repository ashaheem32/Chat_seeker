-- ============================================================================
-- ChatLens - Postgres bootstrap
-- ----------------------------------------------------------------------------
-- Runs once on first container start (mounted into
-- /docker-entrypoint-initdb.d). Subsequent schema changes go through Alembic
-- migrations, not this file.
-- ============================================================================

-- pgvector for semantic search over chat messages.
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm enables fast LIKE / fuzzy text search on message content.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- citext: case-insensitive text - useful for participant names / handles.
CREATE EXTENSION IF NOT EXISTS citext;
