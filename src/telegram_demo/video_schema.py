"""Additive schema for video evidence; independent of optional model dependencies."""
VIDEO_SCHEMA = """
CREATE TABLE IF NOT EXISTS video_analyses (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL, pipeline_version TEXT NOT NULL,
    message_row_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    source_path TEXT NOT NULL, video_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('extracted','ready','partial','error')),
    artifact_dir TEXT NOT NULL, options_json TEXT NOT NULL,
    media_json TEXT NOT NULL, transcript_json TEXT NOT NULL,
    vision_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS idx_video_sha ON video_analyses(video_sha256);
CREATE TABLE IF NOT EXISTS video_classifications (
    id INTEGER PRIMARY KEY,
    video_analysis_id INTEGER REFERENCES video_analyses(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL, batch_id TEXT NOT NULL,
    provider TEXT NOT NULL, model TEXT NOT NULL, backend_key TEXT NOT NULL,
    rubric_version TEXT NOT NULL, rubric_json TEXT NOT NULL,
    evidence_sha256 TEXT NOT NULL, evidence_json TEXT NOT NULL,
    source_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('success','error')),
    label TEXT, probabilities_json TEXT, response_json TEXT, error_code TEXT,
    CHECK((status='success' AND label IS NOT NULL) OR
          (status='error' AND label IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_video_classification_cache
    ON video_classifications(video_analysis_id,evidence_sha256,rubric_version,backend_key,status);
"""
