#!/usr/bin/env bash
# 电商独立站管理脚本：start / restart / stop
#
# 用法:
#   ./run.sh start       启动服务（后台运行）
#   ./run.sh restart     重启服务（先停止再启动）
#   ./run.sh stop        关闭服务
#   ./run.sh status      查看服务状态
#
# 【为什么从服务器外部无法访问？】
# 之前用 --host 127.0.0.1 只会绑定「回环地址」，仅允许本机访问，
# 服务器的公网/内网 IP 及外部浏览器都连不进来。
# 这里改为 --host 0.0.0.0，监听所有网络接口，外网即可通过
# http://服务器IP:8010 访问（需同时放行防火墙/安全组的 8010 端口）。
set -e

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8010}"

cd "$(dirname "$0")"

# PID 与日志文件（位于项目目录下）
PID_FILE="${PID_FILE:-./run.pid}"
LOG_FILE="${LOG_FILE:-./run.log}"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$*"
}

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        log "服务已在运行 (PID: $(cat "$PID_FILE"))，无需重复启动"
        return 0
    fi
    [ -f "$PID_FILE" ] && rm -f "$PID_FILE"   # 清理失效的 PID 文件
    log "启动电商独立站: http://${HOST}:${PORT}  (外网用服务器 IP 访问)"
    nohup .venv/bin/uvicorn main:app --host "$HOST" --port "$PORT" --log-config logging.json >>"$LOG_FILE" 2>&1 &
    echo "$!" > "$PID_FILE"
    log "服务已启动 (PID: $(cat "$PID_FILE"))，日志见 $LOG_FILE"
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID="$(cat "$PID_FILE")"
        if kill -0 "$PID" 2>/dev/null; then
            log "正在关闭服务 (PID: $PID)"
            # 先发 SIGTERM 优雅退出，等最多 5 秒
            kill "$PID" 2>/dev/null || true
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                if ! kill -0 "$PID" 2>/dev/null; then
                    break
                fi
                sleep 0.5
            done
            if kill -0 "$PID" 2>/dev/null; then
                log "优雅退出超时，强制终止 (PID: $PID)"
                kill -9 "$PID" 2>/dev/null || true
            fi
        else
                log "进程 (PID: $PID) 已不存在，清理 PID 文件"
        fi
        rm -f "$PID_FILE"
            log "服务已停止"
    else
            log "未找到 PID 文件，服务当前未在运行"
    fi
}

restart() {
    stop
    start
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        log "服务运行中 (PID: $(cat "$PID_FILE"))，地址 http://${HOST}:${PORT}"
    else
        log "服务未运行"
        [ -f "$PID_FILE" ] && rm -f "$PID_FILE"
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) restart ;;
    status)  status ;;
    *) echo "用法: $0 {start|stop|restart|status}" >&2; exit 1 ;;
esac