#!/usr/bin/env bash
# install.sh — actop 原子安装与卸载脚本 (从仓库安装)
set -euo pipefail

REPO_DIR=$(cd -P "$(dirname "$0")" && pwd -P)
BIN_DIR="${BIN_DIR:-${HOME}/bin}"

# 获取版本号
VERSION="$(
  awk '
    /^\[project\]/ { in_project=1; next }
    /^\[/ && in_project { in_project=0 }
    in_project && $1 == "version" {
      gsub(/"/, "", $3)
      print $3
      exit
    }
  ' "${REPO_DIR}/pyproject.toml" 2>/dev/null || echo "dev"
)"

# -----------------------------------------------------------------------------
# 卸载逻辑 (--uninstall)
# -----------------------------------------------------------------------------
if [[ "${1:-}" == "--uninstall" ]]; then
    echo "==> 正在卸载 actop (v${VERSION})..."
    target="${BIN_DIR}/actop"
    if [[ -L "$target" || -f "$target" ]]; then
        rm -f "$target"
        echo "    ✔ 已删除二进制软链: $target"
    else
        echo "    ℹ 未检测到已安装的 $target"
    fi

    # 清理遗留的 Homebrew 残留（若有）
    if command -v brew >/dev/null 2>&1; then
        if brew list actop >/dev/null 2>&1; then
            echo "    ==> 检测到遗留的 Homebrew actop，正在清理..."
            brew uninstall actop >/dev/null 2>&1 || true
            echo "    ✔ 已清理 Homebrew actop"
        fi
    fi

    echo "🎉 卸载完成！actop 已从系统 PATH (${BIN_DIR}) 中移除。"
    exit 0
fi

# -----------------------------------------------------------------------------
# 安装逻辑 (默认)
# -----------------------------------------------------------------------------
echo "==> 1. 检查硬件与运行时平台..."
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "    ❌ 错误: actop 仅支持 macOS 平台 (当前系统: $(uname -s))" >&2
    exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "    ❌ 错误: actop 专为 Apple Silicon (M1/M2/M3/M4) 设计，不支持 $(uname -m)" >&2
    exit 1
fi
echo "    ✔ 运行环境确认: macOS Apple Silicon ($(uname -m))"

echo "==> 2. 检查 Python 运行环境..."
PYTHON_BIN=""
if command -v uv >/dev/null 2>&1; then
    echo "    ✔ 检测到 uv: $(uv --version | head -n 1)"
elif command -v python3 >/dev/null 2>&1; then
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 11 ]]; then
        PYTHON_BIN="python3"
        echo "    ✔ 检测到 Python 3: $(python3 --version)"
    else
        echo "    ❌ Python 版本过低: Python $PY_VER (actop 要求 Python >= 3.11)" >&2
        exit 1
    fi
else
    echo "    ❌ 未检测到 Python 运行环境。请安装 Python 3.11+ 或 uv (brew install uv)" >&2
    exit 1
fi

echo "==> 3. 初始化并配置虚拟环境 (.venv)..."
if command -v uv >/dev/null 2>&1; then
    if [[ ! -d "${REPO_DIR}/.venv" ]]; then
        uv venv "${REPO_DIR}/.venv"
    fi
    uv pip install --quiet --python "${REPO_DIR}/.venv" -e "${REPO_DIR}"
elif [[ -n "$PYTHON_BIN" ]]; then
    if [[ ! -d "${REPO_DIR}/.venv" ]]; then
        "$PYTHON_BIN" -m venv "${REPO_DIR}/.venv"
    fi
    "${REPO_DIR}/.venv/bin/pip" install --quiet -e "${REPO_DIR}"
fi
echo "    ✔ 依赖与可编辑模块安装就绪"

echo "==> 4. 安装可执行文件启动器至 ${BIN_DIR}/actop..."
mkdir -p "${BIN_DIR}"
mkdir -p "${REPO_DIR}/bin"

# 确保 repo 启动器脚本存在且可执行
cat << 'EOF' > "${REPO_DIR}/bin/actop"
#!/usr/bin/env bash
set -euo pipefail

# Resolve symlinks to find real repo directory
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
REPO_DIR="$(cd -P "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"

if [[ -x "${REPO_DIR}/.venv/bin/actop" ]]; then
    exec "${REPO_DIR}/.venv/bin/actop" "$@"
elif [[ -x "${REPO_DIR}/.venv/bin/python" ]]; then
    exec "${REPO_DIR}/.venv/bin/python" -m actop.actop "$@"
elif command -v uv >/dev/null 2>&1; then
    exec uv run --directory "${REPO_DIR}" actop "$@"
else
    echo "Error: actop environment not initialized. Please run '${REPO_DIR}/install.sh' first." >&2
    exit 1
fi
EOF
chmod +x "${REPO_DIR}/bin/actop"

# 建立软链至 $BIN_DIR/actop
ln -sfn "${REPO_DIR}/bin/actop" "${BIN_DIR}/actop"
echo "    ✔ 已建立软链: ${BIN_DIR}/actop -> ${REPO_DIR}/bin/actop"

echo "==> 5. 自检与健康验证..."
ACTOP_CMD="${BIN_DIR}/actop"
if ! "$ACTOP_CMD" --version >/dev/null 2>&1; then
    echo "    ❌ actop --version 执行失败" >&2
    exit 1
fi

REPORTED_VER=$("$ACTOP_CMD" --version 2>&1 | head -n 1)
echo "    ✔ CLI 响应正常: ${REPORTED_VER}"

if ! "$ACTOP_CMD" --compact >/dev/null 2>&1; then
    echo "    ❌ actop --compact 硬件指标采样验证失败" >&2
    exit 1
fi
echo "    ✔ Apple Silicon 硬件采样正常 (--compact 测试通过)"

echo ""
echo "🎉 安装完成！actop (${REPORTED_VER}) 已成功安装并可在任意终端直接运行。"
echo "常用命令:"
echo "  actop           # 启动实时监控"
echo "  actop --once    # 打印单次 ASCII 快照"
echo "  actop --compact # 输出高密度 JSON (面向 AI Agent)"
