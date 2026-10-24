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
`attempts>1` 或出现 `422` → 查 GitHub 状态；`405 conflict` → 可能有同名文件冲突。

## 三个已知风险（还没修，等门槛到了再动）

### 1. 并发上传同名文件会互相覆盖（最严重，静默丢数据）

- 条件：同一个 `类型目录` + 同一个「年份-标题」；第二个 job 在第一个 job 分配名字之后、
  `cv_pull` 让它可见之前开始。窗口 = worker 最多 60s + push 约 40s + 合并最多 5min + 拉回最多 10min ≈ **0–20 分钟**。
- 后果：两个 PR 都新增同一路径且内容不同 → 405 卡住；或后合并者**静默覆盖**前者。
- **动手门槛（任一先到就修）**：单日上传 ≥5 / 同目录 30 分钟内 ≥2 个 job / 资料 ≥50 份 / 出现重复 `rel` 或 405。
- 修法方向：上传时用「跨进程预留表 + flock」（锁只能放 `/srv/cv-inbox/.reserve/`，
  因为服务是 `ProtectSystem=strict` + `PrivateTmp=yes`，`/tmp` 与 `/var/lock` 都不可共享），
  第二个 job 自动拿 `-2`。`gitee_import.py` 也要接入同一张表，否则只修一半。

### 2. `cv_push3.py` 重试会 422（已修，保留说明）

- 原来先建分支再建 PR，PR 失败后重试必然 422 `Reference already exists`，3 次后永久失败。
- 已改为：建分支前 GET，存在则 `PATCH force`；PR 创建遇 422/409 则复用已有 open PR。
- 顺带修了 `state=all` 的老坑：已关闭的 PR 曾被当成成功，job 写 `ok` 但内容没进 main。

### 3. 上传页超过 90 秒会丢任务号（未修）

- 现在上传页 90 秒后显示「还在处理，稍后刷新页面看结果」，但刷新后无法再查该 job。
- 按当前 push 约 34–40 秒估算，单个 job 约有 7–17% 概率超 90 秒；队列里排第二个几乎必然超。
- 修法方向：前端把 job id 写进 `localStorage` 与 `?job=`，去掉 90 秒硬截止（改成 48h 上限）；
  后端 `/api/job` 对不存在的 id 返回 404 `state=expired`，不要永远 `pending`。

## 回滚

| 改了什么 | 回滚 |
|---|---|
| `cv_push3.py` | `cp -a /root/cv_push3.py.bak.<ts> /root/cv_push3.py`，无需重启 |
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
