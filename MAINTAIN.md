# 维护者须知（MAINTAIN）

给以后接手的人：出问题先看这里。

## 日常监控（全部只读，不改业务）

```bash
# 队列深度：有 job.json 但没有 result.json 的
sudo find /srv/cv-inbox -mindepth 1 -maxdepth 1 -type d -name '[0-9a-f]*' \
  -exec sh -c '[ -f "$1/result.json" ] || echo "$1"' _ {} \;

# 即将撞名：跨 job 出现相同的 rel
sudo grep -h '"rel"' /srv/cv-inbox/*/job.json 2>/dev/null | sort | uniq -d

# 推送重试 / 永久失败
sudo grep -E 'will retry|failed permanently|HTTP 422' /var/log/cv_queue.log

# 合并冲突 / 卡住的 PR
sudo grep -E 'merge conflicts|合并失败|405' /var/log/cv_auto_merge.log

# 端到端延迟（秒）
sudo python3 - <<'PY'
import glob, json, os
for p in glob.glob('/srv/cv-inbox/*/result.json'):
    d = os.path.dirname(p)
    try:
        j = json.load(open(os.path.join(d, 'job.json')))
        r = json.load(open(p))
        print(os.path.basename(d), r.get('at', 0) - j.get('created', 0))
    except Exception:
        pass
PY
```

**告警线**：重复 `rel` ≥1 → 立刻人工处理；队列深度 ≥2 持续 2 分钟 → 看 worker 是否卡住；
`attempts>1` 且最终 `failed permanently`，或日志里出现未处理的 `422` → 查 GitHub 状态；
如果只是 `/tmp/last_pr2.txt` 里的 `MERGED-ALREADY`，那是 PR 已自动合并后的正常重试，不用处理；
`405 conflict` → 可能有同名文件冲突。

## 已知风险与修复状态（1 未修；2、3、4 已修）

### 1. 并发上传同名文件会互相覆盖（未修，最严重，静默丢数据）

- 条件：同一个 `类型目录` + 同一个「年份-标题」；第二个 job 在第一个 job 分配名字之后、
  `cv_pull` 让它可见之前开始。窗口 = worker 最多 60s + push 约 40s + 合并最多 5min + 拉回最多 10min ≈ **0–20 分钟**。
- 后果：两个 PR 都新增同一路径且内容不同 → 405 卡住；或后合并者**静默覆盖**前者。
- **动手门槛（任一先到就修）**：单日上传 ≥5 / 同目录 30 分钟内 ≥2 个 job / 资料 ≥50 份 / 出现重复 `rel` 或 405。
- 修法方向：上传时用「跨进程预留表 + flock」（锁只能放 `/srv/cv-inbox/.reserve/`，
  因为服务是 `ProtectSystem=strict` + `PrivateTmp=yes`，`/tmp` 与 `/var/lock` 都不可共享），
  第二个 job 自动拿 `-2`。`gitee_import.py` 也要接入同一张表，否则只修一半。

### 2. `cv_push3.py` 重试 422（已修，含 P0-2 已合并重试）

- 原来先建分支再建 PR，PR 失败后重试必然 422 `Reference already exists`，3 次后永久失败。
- 已改为：建分支前 GET，存在则 `PATCH force`；PR 创建遇 422/409 则先复用已有 open PR。
- P0-2（已修）：上一轮 PR 被自动合并后，main 已包含本次内容；同一 `CV_JOB_ID` 重试时
  `pulls?state=open` 为空，新分支与 main 没有 diff，GitHub 返回
  `422 No commits between main and patch-job-...`。现在遇到 422/409 会读取分支 head tree
  与当前 main tree，**只有两者相等**才视为「内容已在 main」：
  - 删除刚建的无 diff 分支（`DELETE git/refs/heads/<branch>`；失败只打印、吞掉异常）；
  - stdout 最后一行输出 `ALREADY_MERGED ...`，worker 以该行作为 `pr` 写入 `ok:true`；
  - `/tmp/last_pr2.txt` 写 `MERGED-ALREADY <branch>`（正常成功仍是 `<PR号> <branch>`）。
  - tree 不相等时照旧抛错重试，不会把真实失败误判成成功。
- 顺带修了 `state=all` 的老坑：已关闭的 PR 曾被当成成功，job 写 `ok` 但内容没进 main。

### 3. 上传页丢任务号（已修）

- 原问题：上传页 90 秒后显示「还在处理，稍后刷新页面看结果」，但刷新后无法再查该 job。
- 按当前 push 约 34–40 秒估算，单个 job 约有 7–17% 概率超 90 秒；队列里排第二个几乎必然超。
- 已修：前端把 job id 写进 `localStorage` 与 `?job=`，去掉 90 秒硬截止（改成 48h 上限）；
  后端 `/api/job` 对不存在的 id 返回 404 `state=expired`，不要永远 `pending`。
- 现状（既有验证）：`?job=<真实id>` 能恢复并显示归档结果；`?job=000...0` 显示
  「任务记录已过期（结果只保留 48 小时），请重新上传。」

### 4. 课程内搜索与全角高亮（已修）

- 课程内搜索纯标点：`render(key)` 原来用 `nk = norm(key)` 过滤；`norm('---')` /
  `norm('《》()')` 得到空串，`indexOf('') === 0` 导致全部资料通过。现在「输入非空但
  `nk` 为空」直接视为零结果，显示「这里没有匹配的资料」；输入 `实验` 等正常关键词不受影响。
- `mark()` 全角高亮：原来在 `raw.toLowerCase()` 上定位，搜索 `python` 命中
  `Ｐｙｔｈｏｎ` 也找不到下标。现在定位前做「NFKC + 小写」折叠，且只在折叠前后长度
  一致（1:1）时使用；下标仍对应原串，继续按「原文定位 → 切三段 → 分别 `esc`」输出，
  所以 `&amp;` 不会被切坏。折叠后长度变化的极少数字符退回原文小写，宁可不高亮也不切错。
- 现状（已验证）：课程页 `#cq` 输入 `---` 列表为空；全局搜 `python` 时
  `<mark>Ｐｙｔｈｏｎ</mark>`；搜 `解剖` 中文高亮正常；含 `&` 的假课程名实体未被二次转义。

## 回滚

| 改了什么 | 回滚 |
|---|---|
| `cv_push3.py` | `cp -a /root/cv_push3.py.bak.<ts> /root/cv_push3.py`，无需重启 |
| `docs/index.html` | `cp -a /root/fix3_backups_1792892177/index.html.bak.1792892177 /root/course-vault/docs/index.html`，再按静态发布流程同步到 `/var/www/course-vault/docs/`（纯静态，无需重启） |
| `MAINTAIN.md` | `cp -a /root/fix3_backups_1792892177/MAINTAIN.md.bak.1792892177 /root/course-vault/MAINTAIN.md` |
| `cv_upload_server.py` | 恢复备份 + `systemctl restart cv_upload`（约 30 秒） |
| nginx 配置 | `cp -a /etc/nginx/sites-available/course-vault.bak.<ts> ...` + `nginx -t && systemctl reload nginx` |

## 部署注意

线上真正运行的是 `/srv/cv-upload/cv_upload_server.py`，不是 `/root` 下那份。
改完 `/root` 的源必须再 `install -m644` 到 `/srv/cv-upload/` 然后 `systemctl restart cv_upload`，
否则跑的还是旧代码（这个坑踩过）。

## 站点结构速查

```
/                          302 → /docs/
/c                         302 → /docs/（短链接，带 $is_args$args 保留搜索词）
/up                        302 → /docs/upload.html
/docs/                     搜索 + 浏览（纯静态）
/docs/upload.html          上传
/courses/                  资料原始文件（autoindex）
/_thumb/                   缩略图（nginx 从 /var/www/course-vault-thumbs 提供）
/api/                      上传后端（Flask，127.0.0.1:8090）
/scripts /tests /_template /.github   一律 404（维护者看 Gitee/GitHub）
```
