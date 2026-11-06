-- 仅在部署环境已经提供 pgvector 且明确启用时执行，不下载任何扩展。
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
SET LOCAL search_path = sandbox, public, pg_catalog;
CREATE TABLE sandbox.chunk_embeddings (
    id uuid PRIMARY KEY,
    created_at timestamptz(3) NOT NULL DEFAULT clock_timestamp(),
    chunk_id uuid NOT NULL,
    embedding_space_id uuid NOT NULL,
    dimension integer NOT NULL CHECK (dimension > 0),
    embedding public.vector NOT NULL,
    source_content_sha256 sandbox.sha256_text NOT NULL,
    FOREIGN KEY (chunk_id, source_content_sha256) REFERENCES sandbox.document_chunks(id, content_sha256),
    FOREIGN KEY (embedding_space_id, dimension) REFERENCES sandbox.embedding_spaces(id, dimension),
    UNIQUE (chunk_id, embedding_space_id),
    CHECK (public.vector_dims(embedding) = dimension)
);
CREATE TRIGGER chunk_embeddings_guard BEFORE UPDATE OR DELETE ON sandbox.chunk_embeddings
    FOR EACH ROW EXECUTE FUNCTION sandbox.guard_record_mutation();
CREATE INDEX chunk_embeddings_space_idx ON sandbox.chunk_embeddings(embedding_space_id, chunk_id);
COMMENT ON TABLE sandbox.chunk_embeddings IS '片段向量；未选定模型维度，不预建近似索引，余弦零向量及实际模型来源由应用验证';
