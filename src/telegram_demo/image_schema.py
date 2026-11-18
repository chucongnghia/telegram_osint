"""Additive standalone image evidence schema; no optional dependencies."""
IMAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_analyses (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL, pipeline_version TEXT NOT NULL,
    message_row_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    source_path TEXT NOT NULL, image_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ready','partial','error')),
    artifact_dir TEXT NOT NULL, options_json TEXT NOT NULL,
    media_json TEXT NOT NULL, ocr_json TEXT NOT NULL,
    vision_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS idx_image_sha ON image_analyses(image_sha256);
CREATE TABLE IF NOT EXISTS image_classifications (
    id INTEGER PRIMARY KEY,
    image_analysis_id INTEGER REFERENCES image_analyses(id) ON DELETE SET NULL,
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
CREATE INDEX IF NOT EXISTS idx_image_classification_cache
    ON image_classifications(image_analysis_id,evidence_sha256,rubric_version,backend_key,status);
"""
