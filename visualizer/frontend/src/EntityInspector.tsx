import { useEffect, useEffectEvent, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { runPath, type RunSection } from "./api";

export type RelatedEntity = {
  id: string;
  label: string;
  view: Exclude<RunSection, "overview">;
};
export type InspectorEntity = {
  id: string;
  type: string;
  status?: string;
  content?: unknown;
  lifecycle?: unknown;
  derived?: unknown;
  provenance?: unknown;
  confidence?: number | null;
  warnings?: Array<{ code: string; message: string }>;
  raw: unknown;
  related: RelatedEntity[];
};

export function EntityInspector({
  runId,
  selectionId,
  entity,
  onClose,
}: {
  runId: string;
  selectionId: string | null;
  entity: InspectorEntity | null;
  onClose: () => void;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const [copyState, setCopyState] = useState("Copy JSON");
  const closeInspector = useEffectEvent(onClose);

  useEffect(() => {
    if (!selectionId) return;
    returnFocus.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    closeButton.current?.focus();
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") closeInspector();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      returnFocus.current?.focus();
    };
  }, [selectionId]);

  if (!selectionId) return null;
  const json = entity ? JSON.stringify(entity.raw, null, 2) : "";
  return (
    <aside
      className="entity-inspector"
      role="dialog"
      aria-modal="false"
      aria-label={`Entity Inspector: ${selectionId}`}
    >
      <header>
        <div>
          <p className="eyebrow">Sanitized read-only data</p>
          <h2>Entity Inspector</h2>
        </div>
        <button
          ref={closeButton}
          onClick={onClose}
          aria-label="Close Entity Inspector"
        >
          Close <kbd>Esc</kbd>
        </button>
      </header>
      {!entity ? (
        <section className="inspector-missing" role="status">
          <h3>Entity unavailable</h3>
          <p>
            <code>{selectionId}</code> is not present in this view. The run
            remains available.
          </p>
        </section>
      ) : (
        <>
          <dl className="inspector-summary">
            <InspectorRow label="ID" value={entity.id} />
            <InspectorRow label="Type" value={entity.type} />
            <InspectorRow
              label="Status"
              value={entity.status || "Not recorded"}
            />
            <InspectorRow
              label="Confidence"
              value={
                entity.confidence == null
                  ? "Not recorded"
                  : String(entity.confidence)
              }
            />
          </dl>
          <InspectorSection title="Content" value={entity.content} />
          <InspectorSection title="Lifecycle" value={entity.lifecycle} />
          <InspectorSection title="Derived" value={entity.derived} />
          <InspectorSection title="Provenance" value={entity.provenance} />
          {entity.warnings && entity.warnings.length > 0 && (
            <section className="inspector-section">
              <h3>Warnings</h3>
              <ul>
                {entity.warnings.map((warning, index) => (
                  <li key={`${warning.code}-${index}`}>
                    <strong>{warning.code}</strong>: {warning.message}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section className="inspector-section">
            <h3>Related entities</h3>
            {entity.related.length === 0 ? (
              <p>None recorded.</p>
            ) : (
              <nav aria-label="Related entities">
                {entity.related.map((related) => (
                  <Link
                    key={`${related.view}-${related.id}`}
                    to={entityHref(runId, related)}
                  >
                    {related.label}: <code>{related.id}</code>
                  </Link>
                ))}
              </nav>
            )}
          </section>
          <section className="inspector-section inspector-raw">
            <div>
              <h3>Sanitized raw JSON</h3>
              <button
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(json);
                    setCopyState("Copied");
                  } catch {
                    setCopyState("Copy failed");
                  }
                }}
              >
                {copyState}
              </button>
            </div>
            <pre>{json}</pre>
          </section>
        </>
      )}
    </aside>
  );
}

export function entityHref(runId: string, related: RelatedEntity): string {
  return `${runPath(runId, related.view)}?entity=${encodeURIComponent(related.id)}`;
}
function InspectorRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
function InspectorSection({ title, value }: { title: string; value: unknown }) {
  if (
    value == null ||
    (typeof value === "object" && Object.keys(value).length === 0)
  )
    return null;
  return (
    <section className="inspector-section">
      <h3>{title}</h3>
      <pre>
        {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
      </pre>
    </section>
  );
}
