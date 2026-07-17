import { useEffect, useState } from "react";

type HealthResponse = {
  status: "ok";
  service: string;
  api_version: string;
};

type ConnectionState =
  | { kind: "connecting" }
  | { kind: "connected"; health: HealthResponse }
  | { kind: "unavailable" };

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const health = value as Record<string, unknown>;
  return health.status === "ok" && typeof health.service === "string" && typeof health.api_version === "string";
}

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>({ kind: "connecting" });

  useEffect(() => {
    const controller = new AbortController();

    async function checkBackend() {
      try {
        const response = await fetch("/api/v1/health", { signal: controller.signal });
        const body: unknown = await response.json();
        if (!response.ok || !isHealthResponse(body)) {
          throw new Error("Unexpected health response");
        }
        setConnection({ kind: "connected", health: body });
      } catch (error: unknown) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setConnection({ kind: "unavailable" });
        }
      }
    }

    void checkBackend();
    return () => controller.abort();
  }, []);

  return (
    <main className="shell">
      <section className="masthead" aria-labelledby="page-title">
        <p className="eyebrow">Read-only experiment workspace</p>
        <h1 id="page-title">VillagerAgent Visualizer</h1>
        <p className="lede">Inspect recorded Minecraft runs without changing runtime state.</p>
      </section>

      <section className={`status-panel status-panel--${connection.kind}`} aria-live="polite">
        <span className="status-mark" aria-hidden="true" />
        <div>
          {connection.kind === "connecting" && (
            <>
              <h2>Connecting to backend</h2>
              <p>Checking the local visualizer API.</p>
            </>
          )}
          {connection.kind === "connected" && (
            <>
              <h2>Backend connected</h2>
              <p>{connection.health.service} · API {connection.health.api_version}</p>
            </>
          )}
          {connection.kind === "unavailable" && (
            <>
              <h2>Backend unavailable</h2>
              <p>The interface remains available. Start the visualizer backend and refresh this page.</p>
            </>
          )}
        </div>
      </section>
    </main>
  );
}
