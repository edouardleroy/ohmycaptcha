#!/bin/bash
# OhMyCaptcha management script
# Usage: ./ohmycaptcha.sh {start|stop|status|restart}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PID_FILE="$SCRIPT_DIR/ohmycaptcha.pid"
LOG_FILE="/var/log/ohmycaptcha.log"
ENV_FILE="$SCRIPT_DIR/.env"

ensure_env() {
    if [ ! -f "$ENV_FILE" ]; then
        echo "ERROR: .env file not found at $ENV_FILE"
        exit 1
    fi
    set -a
    source "$ENV_FILE"
    set +a
}

start() {
    ensure_env
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "OhMyCaptcha is already running (PID: $(cat "$PID_FILE"))"
        exit 1
    fi
    echo "Starting OhMyCaptcha..."
    mkdir -p "$(dirname "$LOG_FILE")"
    cd "$SCRIPT_DIR"
    nohup "$VENV_DIR/bin/python" main.py >> "$LOG_FILE" 2>&1 &
    PID=$!
    echo $PID > "$PID_FILE"
    sleep 3
    if kill -0 $PID 2>/dev/null; then
        echo "✓ Started (PID: $PID)"
        echo "Logs: $LOG_FILE"
        echo "API: http://localhost:8000"
    else
        echo "✗ Failed to start. Check logs: $LOG_FILE"
        tail -20 "$LOG_FILE"
        rm -f "$PID_FILE"
    fi
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "OhMyCaptcha is not running (no PID file)"
        return
    fi
    PID=$(cat "$PID_FILE")
    echo "Stopping OhMyCaptcha (PID: $PID)..."
    kill $PID 2>/dev/null
    for i in $(seq 1 10); do
        if ! kill -0 $PID 2>/dev/null; then
            echo "✓ Stopped"
            rm -f "$PID_FILE"
            return
        fi
        sleep 1
    done
    echo "Force killing..."
    kill -9 $PID 2>/dev/null
    rm -f "$PID_FILE"
    echo "✓ Force stopped"
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        PID=$(cat "$PID_FILE")
        echo "OhMyCaptcha is RUNNING (PID: $PID)"
        echo "API: http://localhost:8000"
        echo "Health: http://localhost:8000/api/v1/health"
        # Check if responding
        curl -sf http://localhost:8000/api/v1/health 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (not responding yet)"
        return 0
    else
        echo "OhMyCaptcha is STOPPED"
        rm -f "$PID_FILE"
        return 1
    fi
}

restart() {
    stop
    sleep 2
    start
}

case "${1:-status}" in
    start)   start ;;
    stop)    stop ;;
    status)  status ;;
    restart) restart ;;
    *)       echo "Usage: $0 {start|stop|status|restart}" ;;
esac
