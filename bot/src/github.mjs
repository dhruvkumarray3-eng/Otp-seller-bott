import { readdir, readFile } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { config } from "./config.mjs";

const GITHUB_API = "https://api.github.com";
const ignoredDirectories = new Set([".git", "node_modules", ".cache", "dist", "build"]);
const ignoredFiles = new Set([".env", ".env.local", ".env.production"]);

const headers = (token, json = false) => ({
  Accept: "application/vnd.github+json",
  "User-Agent": "github-workflow-telegram-bot",
  ...(json ? { "Content-Type": "application/json" } : {}),
  Authorization: `Bearer ${token}`,
});

const api = async (path, token, options = {}) => {
  const response = await fetch(`${GITHUB_API}${path}`, {
    ...options,
    headers: { ...headers(token, Boolean(options.body)), ...(options.headers || {}) },
  });
  const bodyText = await response.text();
  let body;
  try {
    body = bodyText ? JSON.parse(bodyText) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    const message = body?.message || `GitHub returned HTTP ${response.status}`;
    throw new Error(message);
  }
  return body;
};

export const parseRepositoryUrl = (value) => {
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "github.com") return null;
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length < 2) return null;
    const owner = parts[0];
    const name = parts[1].replace(/\.git$/i, "");
    if (!/^[A-Za-z0-9_.-]+$/.test(owner) || !/^[A-Za-z0-9_.-]+$/.test(name)) return null;
    return { owner, name };
  } catch {
    return null;
  }
};

const safeError = (error) => {
  const message = String(error?.message || error);
  return message.replace(/gh[pousr]_[A-Za-z0-9_]+/g, "[redacted-token]");
};

const walk = async (root, current = root) => {
  const entries = await readdir(current, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    if (ignoredDirectories.has(entry.name) || ignoredFiles.has(entry.name)) continue;
    const fullPath = join(current, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(root, fullPath)));
    else if (entry.isFile()) files.push({ fullPath, relativePath: relative(root, fullPath) });
  }
  return files;
};

export const validateAndPush = async ({ repositoryUrl, token }) => {
  const repository = parseRepositoryUrl(repositoryUrl);
  if (!repository) throw new Error("Please send a valid public GitHub repository URL.");
  if (!token || token.trim().length < 10) throw new Error("The GitHub token looks incomplete.");

  const repo = await api(`/repos/${encodeURIComponent(repository.owner)}/${encodeURIComponent(repository.name)}`, token);
  const branch = repo.default_branch || "main";
  const root = resolve(config.pushRoot);
  const sourceFiles = await walk(root);
  if (!sourceFiles.length) throw new Error("No pushable files were found in the configured folder.");

  let pushed = 0;
  for (const file of sourceFiles) {
    const content = await readFile(file.fullPath);
    if (content.byteLength > 2_000_000) continue;
    const remotePath = [config.pushRemoteRoot, file.relativePath].filter(Boolean).join("/").split("\\").join("/");
    let existingSha;
    try {
      const existing = await api(
        `/repos/${encodeURIComponent(repository.owner)}/${encodeURIComponent(repository.name)}/contents/${remotePath}?ref=${encodeURIComponent(branch)}`,
        token,
      );
      existingSha = existing?.sha;
    } catch {
      existingSha = undefined;
    }
    await api(
      `/repos/${encodeURIComponent(repository.owner)}/${encodeURIComponent(repository.name)}/contents/${remotePath}`,
      token,
      {
        method: "PUT",
        body: JSON.stringify({
          message: `chore: sync bot workflow files (${new Date().toISOString().slice(0, 10)})`,
          content: content.toString("base64"),
          branch,
          ...(existingSha ? { sha: existingSha } : {}),
        }),
      },
    );
    pushed += 1;
  }

  return {
    owner: repository.owner,
    name: repository.name,
    branch,
    pushed,
    skipped: sourceFiles.length - pushed,
    url: `https://github.com/${repository.owner}/${repository.name}`,
  };
};

export const formatGithubError = (error) => safeError(error);