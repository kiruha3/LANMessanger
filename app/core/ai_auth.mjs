// Pi owns OAuth credentials and refresh logic. This adapter exposes UI callbacks as JSONL.
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const { AuthStorage, ModelRegistry } = await import(pathToFileURL(process.argv[2]).href);
const storage = AuthStorage.create(join(process.argv[3], "auth.json"));
const write = (value) => process.stdout.write(JSON.stringify(value) + "\n");
let pendingInput;
let login;
let buffer = "";

function ask(message, options) {
  write({ type: "input", message, options });
  return new Promise((resolve) => { pendingInput = resolve; });
}

async function handle(command) {
  if (command.type === "input") {
    const resolve = pendingInput;
    pendingInput = undefined;
    resolve?.(command.value ?? "");
  } else if (command.type === "list") {
    storage.reload();
    const registry = ModelRegistry.create(storage);
    write({ type: "catalog", providers: storage.getOAuthProviders().map(p => ({ id: p.id, name: p.name })),
      authenticated: storage.list(), models: registry.getAll().map(m => ({ id: m.id, name: m.name, provider: m.provider })) });
  } else if (command.type === "login") {
    if (login) throw new Error("Вход уже выполняется");
    login = new AbortController();
    try {
      await storage.login(command.provider, {
        onAuth: (info) => write({ type: "auth", ...info }),
        onPrompt: (prompt) => ask(prompt.message),
        onProgress: (message) => write({ type: "progress", message }),
        onManualCodeInput: () => ask("При необходимости вставьте код или полный URL после входа в браузере"),
        onSelect: (prompt) => ask(prompt.message, prompt.options),
        signal: login.signal,
      });
      write({ type: "logged_in", provider: command.provider });
    } finally { login = undefined; }
  } else if (command.type === "logout") {
    storage.logout(command.provider);
    write({ type: "logged_out", provider: command.provider });
  }
}

process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let end;
  while ((end = buffer.indexOf("\n")) !== -1) {
    const line = buffer.slice(0, end);
    buffer = buffer.slice(end + 1);
    if (!line.trim()) continue;
    try { handle(JSON.parse(line)).catch(error => write({ type: "error", message: error.message })); }
    catch (error) { write({ type: "error", message: error.message }); }
  }
});
process.stdin.on("end", () => { login?.abort(); process.exit(0); });
