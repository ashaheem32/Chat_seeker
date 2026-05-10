"""
Celery task modules.

This package holds tasks that aren't part of the original analysis
pipeline (`app.workers.tasks`) — currently just the language
normalization task introduced with Layer 4. Keeping it as a separate
import root means the Celery worker autodiscovers it via
`celery_app.include = [...]` without us having to dump every task into
the pipeline file."""
