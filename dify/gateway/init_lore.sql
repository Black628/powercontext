-- 记忆酒馆 · 设定库（由 seekdb 承载，供 Dify 工作流检索）
-- 用法：python seed.py 会自动执行本文件

CREATE DATABASE IF NOT EXISTS tavern;
USE tavern;

DROP TABLE IF EXISTS tavern_lore;

CREATE TABLE tavern_lore (
  id      BIGINT AUTO_INCREMENT PRIMARY KEY,
  title   VARCHAR(128) NOT NULL,
  content TEXT NOT NULL,
  tag     VARCHAR(32) NOT NULL DEFAULT '',
  FULLTEXT INDEX ft_lore(content) WITH PARSER ik
) DEFAULT CHARSET = utf8mb4;

INSERT INTO tavern_lore (title, content, tag) VALUES
('老陈是谁', '老陈是「醉三更」酒馆的老板，五十来岁，左脸一道旧疤。他嘴上刻薄，心里记人：谁赊过账、谁摔过杯子、谁上次说过要早睡，他都记得。', 'npc'),
('酒馆规矩', '醉三更有三条规矩：不许在后院放火，不许叫老板「陈叔」，不许在周三点没写过的酒。犯规的人下次进门会被老陈当面点破。', 'rule'),
('招牌酒', '老陈的招牌是「三更醒」，用桂花和青梅泡的。只有熟客能点，生客点了会被他斜眼打量三秒。', 'item'),
('后院的猫', '后院有一只三花猫叫「账本」，是老陈养的。据说它会往谁脚边凑，谁下次就会被老陈记住名字。', 'npc'),
('赊账规矩', '赊账只能赊一次，第二次老陈会先提上一次的账。这条规矩他执行得很死板。', 'rule'),
('老陈的忌讳', '老陈最烦别人提他年轻时在城南开过店的事，一提就沉默，然后给你换一杯更烈的。', 'rule');

-- 预置记忆：演示"跨会话记忆复用"用
-- 玩家「阿狸」上次来过，摔了一个杯子并答应赔
-- 记忆本身写进 PowerContext（见 seed.py），这里只准备设定库
