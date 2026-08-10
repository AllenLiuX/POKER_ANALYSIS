-- 对手剥削报告改用「文本」对手键，与前端 import_entries 现算画像口径一致。
--
-- 背景：画像已统一由 Supabase import_entries 现算，对手主键是稳定的 alias 键（形如
-- "alias:<昵称>"），而不是 opponents 表的 uuid。此前 opponent_reports.opponent_id 是
-- uuid，前端写入 "alias:..." 会因类型不合法而静默失败（.catch 吞掉），导致报告永远不落库、
-- 每次都要重新生成。这里把该列改为 text（保留 (user_id, opponent_id) 主键），使报告可持久化。
--
-- 幂等：可重复执行。已是 text 时 alter type 为 no-op；已有 uuid 值按 ::text 转换保留。

do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'opponent_reports'
      and column_name = 'opponent_id'
      and data_type = 'uuid'
  ) then
    alter table public.opponent_reports
      alter column opponent_id type text using opponent_id::text;
  end if;
end$$;
