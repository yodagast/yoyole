# 部署与反向代理

## 1. 运行方式

应用本身就是一个 uvicorn 进程，`run.sh` 直接把它挂在 `0.0.0.0:8020`：

```bash
./run.sh start      # nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8020
./run.sh status
./run.sh restart
./run.sh stop
```

生产环境通常还会在前面加一层 nginx 做 80/443 → 8020 的反代。
**加了反代之后，上传类接口的成败就不只取决于应用了**，见下面第 3 节。

## 2. nginx 反代配置

```nginx
server {
    listen       80;
    server_name  your-domain.com;

    # ⚠️ 关键项：nginx 默认只有 1m，商品包 / PPT / 视频上传会直接被它拦成 413，
    #    请求根本到不了后端（后端日志里也查不到）。这里要大于应用的上限（见下表）。
    client_max_body_size 900m;
    client_body_timeout  300s;

    location / {
        proxy_pass http://127.0.0.1:8020;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 大包导入要「上传 + 解压 + 写库」，耗时可远超默认 60s
        proxy_read_timeout    600s;
        proxy_send_timeout    600s;
        proxy_connect_timeout 30s;

        # 大文件上传建议关掉请求缓冲：让请求体直接流给后端，
        # 否则 nginx 会先完整落盘一份再转发，大包时磁盘与耗时都翻倍
        proxy_request_buffering off;
    }

    # 静态资源走 nginx 直出，不经过 Python 进程
    location /static/ {
        alias /path/to/yoyole/static/;
        expires 7d;
        access_log off;
    }
}
```

改完执行 `nginx -t && nginx -s reload`。

> 用 Caddy / 宝塔 / 云厂商 SLB / WAF 同理：都要放开「请求体大小」这一项，
> 云 WAF 默认常在 1MB ~ 8MB 之间，比 nginx 还严格。

## 3. 上传上限对照表

应用自身的限制（代码里的常量），反代的 `client_max_body_size` 必须 **大于等于** 最大值：

| 接口 | 用途 | 应用上限 | 超限返回 |
|---|---|---|---|
| `POST /api/upload?kind=image\|detail` | 商品图上传 | 5MB | 413（JSON `detail`） |
| `POST /api/upload?kind=video` | 商品视频上传 | 100MB | 413（JSON `detail`） |
| `POST /api/admin/import/products` | 产品册 PPT 导入 | 200MB | 413（JSON `detail`） |
| `POST /api/admin/product-package/import` | 商品包 zip 导入 | 800MB | 413（JSON `detail`） |
| `POST /api/admin/product-package/import-server` | 服务器上已导出的包 | 无请求体 | — |

## 4. 报 413 怎么查

先分清是谁返回的 —— 看响应体：

| 响应体 | 来源 | 结论 |
|---|---|---|
| `{"detail": "..."}` JSON | 本应用 | 文件确实超过上表上限 |
| HTML / 纯文本错误页 | **反向代理 / 云 WAF** | 请求没到后端，去调 `client_max_body_size` |

最可靠的判据是**看 `run.log`**：

```bash
grep 'POST /api/admin/product-package/import' run.log | tail
```

- 有这条访问日志（哪怕状态是 413）→ 应用返回的；
- **完全没有这条日志** → 请求被前置环节拦下了，应用压根没收到。

后台前端对非 JSON 错误会额外给提示，例如：

> 导入失败（HTTP 413）：请求体被前置服务（nginx 等反向代理）拦下了，后端并未收到这个请求。
> 可把反向代理的 `client_max_body_size` 调大（如 900m），或改用列表里的「导入到本站」直接从服务器导入。

`502` / `504` 同理：504 多为大包导入超过 `proxy_read_timeout`，调大超时即可。

## 5. 顺带一提

- 反向代理修好之前，可以用「商品管理 → 已导出商品包 → **导入到本站**」
  绕过去：商品包本就存放在服务器 `static/exports/`，直接读本地目录导入，
  请求体只有几十字节，不受任何上传限制影响。
- `run.sh` 用的是 `--host 0.0.0.0`，若服务器安全组已放行 8020，
  外部可以绕过 nginx 直接访问后端（也就绕过了所有反代限制）。
  生产环境建议只监听 `127.0.0.1`，统一从 nginx 进出。
