# seekdb 全文索引与分词器

## 全文索引怎么用

建表后可以直接加全文索引，指定分词器：

```sql
ALTER TABLE docs ADD FULLTEXT INDEX fti_ik(title, content) WITH PARSER ik;
ALTER TABLE docs ADD FULLTEXT INDEX fti_en(title_en, content_en) WITH PARSER beng;
```

查询使用 MATCH ... AGAINST：

```sql
SELECT COUNT(*) FROM docs WHERE MATCH(title, content) AGAINST('全文索引 分词器');
```

## 内置分词器

seekdb 自带 ik、beng、space、ngram、ngram2 等分词器。
ik 面向中文，支持 smart 与 max_word 两种模式；beng 面向英文；
ngram 适合没有空格分隔的语言或模糊匹配场景。

## 分词热路径

每次分词都要准备解析器、词典与停用词表。把解析器实例复用、
停用词表做成全局单例、分词中间结构内存池化，可以显著降低单次分词开销。
中文分词的主要成本在查词典和判断停用词，英文分词则集中在大小写转换与切分。

## 混合检索

向量检索擅长语义相近，全文检索擅长关键词精确命中。
把两路结果做 RRF（倒数排名融合）通常比单路更稳：
既不会因为语义漂移漏掉关键词文档，也不会因为生僻词丢掉语义相似项。
