import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendDirectory = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repositoryDirectory = path.dirname(frontendDirectory);
const viteEntry = path.join(frontendDirectory, "node_modules", "vite", "bin", "vite.js");
const localPythons = [".venv", "venv"].map((directory) => (
  process.platform === "win32"
    ? path.join(repositoryDirectory, directory, "Scripts", "python.exe")
    : path.join(repositoryDirectory, directory, "bin", "python")
));
const pythonCandidates = [...new Set([
  ...(process.env.PYTHON ? [process.env.PYTHON] : []),
  ...localPythons.filter(existsSync),
  ...(process.platform === "win32" ? ["python", "py"] : ["python3", "python"]),
])];

const children = new Set();
let stopping = false;

function stop(exitCode = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) child.kill();
  setTimeout(() => process.exit(exitCode), 250);
}

function startBackend(candidates) {
  const [python, ...remaining] = candidates;
  if (!python) {
    return Promise.reject(new Error(
      "Unable to find Python. Create the repository venv or set the PYTHON environment variable.",
    ));
  }

  return new Promise((resolve, reject) => {
    const backend = spawn(
      python,
      ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"],
      { cwd: repositoryDirectory, stdio: "inherit" },
    );
    children.add(backend);
    backend.once("spawn", () => resolve(backend));
    backend.once("error", (error) => {
      children.delete(backend);
      if (error.code === "ENOENT") startBackend(remaining).then(resolve, reject);
      else reject(error);
    });
    backend.once("exit", (code) => {
      children.delete(backend);
      if (!stopping) stop(code ?? 1);
    });
  });
}

async function waitForBackend() {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch("http://127.0.0.1:8000/");
      if (response.ok) return;
    } catch {
      // FastAPI is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("FastAPI did not become ready on port 8000 within 10 seconds.");
}

function startFrontend() {
  const frontend = spawn(process.execPath, [viteEntry], {
    cwd: frontendDirectory,
    stdio: "inherit",
  });
  children.add(frontend);
  frontend.once("error", (error) => {
    console.error(error.message);
    stop(1);
  });
  frontend.once("exit", (code) => {
    children.delete(frontend);
    if (!stopping) stop(code ?? 1);
  });
}

process.once("SIGINT", () => stop(0));
process.once("SIGTERM", () => stop(0));

try {
  await startBackend(pythonCandidates);
  await waitForBackend();
  startFrontend();
} catch (error) {
  console.error(error.message);
  stop(1);
}
