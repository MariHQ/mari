-- Remove the abandoned external knowledge-substrate experiment. Mari owns
-- connector ingestion, documents, retrieval, and lifecycle state directly.
DELETE FROM settings WHERE key = 'knowledge_substrate';

ALTER TABLE facts DROP COLUMN IF EXISTS substrate_document_id;
ALTER TABLE glossary DROP COLUMN IF EXISTS substrate_document_id;

DROP TABLE IF EXISTS substrate_document_tags;
DROP TABLE IF EXISTS substrate_document_watches;
DROP TABLE IF EXISTS substrate_findings;
DROP TABLE IF EXISTS substrate_documents;
DROP TABLE IF EXISTS substrate_sources;
