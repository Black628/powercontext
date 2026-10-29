-- ============================================================
-- Recall（知返）seekdb 初始化脚本
-- 依据 REQUIREMENTS.md 第 3.1 节；向量维度按阶段 1 要求改为 384
-- 用法：mysql -h127.0.0.1 -P2881 -uroot < scripts/init_seekdb.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS recall;
USE recall;

-- 文档切块表：内容 + 全文索引（ik 中文分词）+ 向量索引
CREATE TABLE IF NOT EXISTS chunks (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  doc_id       VARCHAR(64)  NOT NULL,
  doc_name     VARCHAR(256) NOT NULL,
  page         INT          NOT NULL DEFAULT 0,
  offset_in_doc INT         NOT NULL DEFAULT 0,
  content      TEXT         NOT NULL,
  embedding    VECTOR(384),
  created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FULLTEXT INDEX ft_content(content) WITH PARSER ik,
  -- seekdb 要求向量索引显式声明参数；缺少参数会报 1235
  VECTOR INDEX vidx(embedding) WITH (distance=cosine, type=hnsw, lib=vsag)
) DEFAULT CHARSET = utf8mb4;

-- 文档登记表：记录上传文档与索引状态（GET /api/docs 使用）
CREATE TABLE IF NOT EXISTS docs (
  doc_id      VARCHAR(64) PRIMARY KEY,
  doc_name    VARCHAR(256) NOT NULL,
  chunk_count INT DEFAULT 0,
  status      VARCHAR(32) DEFAULT 'pending',
  error       TEXT,
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) DEFAULT CHARSET = utf8mb4;

-- 计数器表：检索次数等运行指标（GET /api/stats 使用）
CREATE TABLE IF NOT EXISTS counters (
  name  VARCHAR(64) PRIMARY KEY,
  value BIGINT DEFAULT 0
) DEFAULT CHARSET = utf8mb4;
