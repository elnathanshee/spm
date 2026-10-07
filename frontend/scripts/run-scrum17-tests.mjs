import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendDirectory = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repositoryDirectory = path.dirname(frontendDirectory);

const localPython = process.platform === "win32"
  ? path.join(repositoryDirectory, "venv", "Scripts", "python.exe")
  : path.join(repositoryDirectory, "venv", "bin", "python");

const candidates = [
  ...(process.env.PYTHON ? [process.env.PYTHON] : []),
  ...(existsSync(localPython) ? [localPython] : []),
  ...(process.platform === "win32" ? ["python", "py"] : ["python3", "python"]),
];

for (const python of [...new Set(candidates)]) {
  const result = spawnSync(
    python,
    ["-m", "pytest", "backend/tests/test_scrum17_rbac.py", "-q"],
    { cwd: repositoryDirectory, stdio: "inherit" },
  );

  if (!result.error) process.exit(result.status ?? 1);
  if (result.error.code !== "ENOENT") throw result.error;
}

console.error(
  "Unable to find Python. Create the repository venv or set the PYTHON environment variable.",
);
process.exit(1);
