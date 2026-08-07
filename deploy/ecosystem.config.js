// pm2 进程编排：后端(FastAPI/uvicorn) + 前端(Next.js)。
//
// 通常不直接调用，由 deploy/start_prod.sh / restart_prod.sh 驱动：
//   pm2 startOrReload deploy/ecosystem.config.js --update-env
//
// 端口/worker 等从环境变量读取（start_prod.sh 会先 source deploy/config.env 再导出）。
// 前提：后端已建好 .venv（start_prod.sh 负责），前端已 npm run build。

const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..");
const BACKEND_DIR = path.join(REPO_ROOT, "backend");
const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");
const LOG_DIR = process.env.LOG_DIR || path.join(REPO_ROOT, "logs");

const BACKEND_HOST = process.env.BACKEND_HOST || "0.0.0.0";
const BACKEND_PORT = process.env.BACKEND_PORT || "8000";
const BACKEND_WORKERS = process.env.BACKEND_WORKERS || "2";
const FRONTEND_HOST = process.env.FRONTEND_HOST || "0.0.0.0";
const FRONTEND_PORT = process.env.FRONTEND_PORT || "3000";

module.exports = {
  apps: [
    {
      name: "poker-backend",
      cwd: BACKEND_DIR,
      // 直接执行 venv 里的 uvicorn 可执行文件（自带 shebang），故 interpreter=none。
      script: path.join(BACKEND_DIR, ".venv", "bin", "uvicorn"),
      interpreter: "none",
      args: [
        "app.main:app",
        "--host", BACKEND_HOST,
        "--port", BACKEND_PORT,
        "--workers", BACKEND_WORKERS,
        "--proxy-headers",
        "--forwarded-allow-ips=*",
        "--no-access-log",
      ].join(" "),
      // uvicorn 自己管理多 worker，pm2 用 fork 单实例守护 master 即可。
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      max_restarts: 10,
      min_uptime: "10s",
      max_memory_restart: "600M",
      env: { PYTHONUNBUFFERED: "1" },
      out_file: path.join(LOG_DIR, "backend.out.log"),
      error_file: path.join(LOG_DIR, "backend.err.log"),
      merge_logs: true,
      time: true,
    },
    {
      name: "poker-frontend",
      cwd: FRONTEND_DIR,
      script: "npm",
      // 等价于：next start -p <port> -H <host>
      args: `run start -- -p ${FRONTEND_PORT} -H ${FRONTEND_HOST}`,
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      max_restarts: 10,
      min_uptime: "10s",
      max_memory_restart: "800M",
      env: { NODE_ENV: "production" },
      out_file: path.join(LOG_DIR, "frontend.out.log"),
      error_file: path.join(LOG_DIR, "frontend.err.log"),
      merge_logs: true,
      time: true,
    },
  ],
};
