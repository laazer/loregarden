import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type McpServerInput, type McpServerView } from "../api/client";
import { McpActivityFeed } from "../components/mcp/McpActivityFeed";
import { McpServerForm } from "../components/mcp/McpServerForm";
import { PageHeroAppToolbar } from "../components/PageHeroAppToolbar";
import "./McpGatewayPage.css";

const LOREGARDEN_SERVER = "loregarden";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}

function ServerRow({
  server,
  selected,
  onSelect,
}: {
  server: McpServerView;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`mcp-server-row${selected ? " selected" : ""}`}
      onClick={onSelect}
    >
      <div className="mcp-server-row-head">
        <span className="mcp-server-name">{server.name}</span>
        {!server.enabled && <span className="state-label">disabled</span>}
        {server.enabled && server.tool_policy === "auto" && (
          <span className="state-label mcp-server-trusted">trusted</span>
        )}
      </div>
      <div className="mcp-server-row-meta">
        {server.transport} · {server.transport === "http" ? server.url : server.command}
      </div>
      {server.auth_env_var && (
        <div
          className={`mcp-server-auth${server.auth_present ? "" : " missing"}`}
          title={
            server.auth_present
              ? `${server.auth_env_var} is set`
              : `${server.auth_env_var} is not set where Loregarden runs`
          }
        >
          {server.auth_env_var} {server.auth_present ? "· set" : "· missing"}
        </div>
      )}
    </button>
  );
}

/**
 * The MCP servers agents can reach, and how they are configured.
 *
 * Deliberately not the full comp. It shows the registry, loregarden's own
 * always-present server, and nothing else: request rates, latency and a live
 * feed need per-call telemetry that does not exist yet (U1d), and a metrics
 * header fed by no measurements is a number the operator would believe.
 */
export function McpGatewayPage() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const servers = useQuery({ queryKey: ["mcp-servers"], queryFn: api.mcpServers });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["mcp-servers"] });

  const create = useMutation({
    mutationFn: (body: McpServerInput) => api.createMcpServer(body),
    onSuccess: (created) => {
      invalidate();
      setAdding(false);
      setSelectedId(created.id);
    },
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<McpServerInput> }) =>
      api.updateMcpServer(id, body),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteMcpServer(id),
    onSuccess: () => {
      invalidate();
      setSelectedId(null);
    },
  });

  const rows = servers.data ?? [];
  const selected = rows.find((s) => s.id === selectedId) ?? null;
  const editing = adding ? null : selected;
  const showForm = adding || Boolean(selected);

  return (
    <div className="screen-view screen-view--mcp">
      <header className="page-hero-header">
        <div className="page-hero-copy">
          <div className="page-hero-eyebrow">
            <span>MCP Gateway</span>
            <span className="page-hero-eyebrow-dot" aria-hidden />
            <span className="page-hero-eyebrow-muted">Register · Configure</span>
          </div>
          <h1 className="page-hero-title">Servers agents can reach</h1>
          <p className="page-hero-sub">
            Registered servers are added to every agent&rsquo;s MCP configuration when it
            starts.
          </p>
        </div>
        <div className="page-hero-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              setAdding(true);
              setSelectedId(null);
            }}
          >
            Add server
          </button>
          <PageHeroAppToolbar />
        </div>
      </header>

      <div className="mcp-page-body">
        <aside className="mcp-servers-rail">
          <div className="state-label">Registered</div>

          {servers.isPending && <div className="mcp-empty">Loading…</div>}
          {servers.isError && <div className="mcp-empty">Could not load the registry.</div>}

          {!servers.isPending && rows.length === 0 && (
            <div className="mcp-empty">
              No servers registered. Agents still reach Loregarden&rsquo;s own tools.
            </div>
          )}

          {rows.map((server) => (
            <ServerRow
              key={server.id}
              server={server}
              selected={server.id === selectedId}
              onSelect={() => {
                setAdding(false);
                setSelectedId(server.id);
              }}
            />
          ))}

          <div className="state-label mcp-builtin-label">Always available</div>
          <div className="mcp-server-row builtin">
            <div className="mcp-server-row-head">
              <span className="mcp-server-name">{LOREGARDEN_SERVER}</span>
              <span className="state-label">built in</span>
            </div>
            <div className="mcp-server-row-meta">
              This control plane&rsquo;s own tools — tickets, artifacts, memory.
            </div>
          </div>
        </aside>

        <section className="mcp-detail">
          {showForm ? (
            <>
              <div className="mcp-detail-head">
                <h2 className="mcp-detail-title">
                  {adding ? "Register a server" : selected?.name}
                </h2>
                {selected && !adding && (
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(selected.id)}
                  >
                    {remove.isPending ? "Removing…" : "Remove"}
                  </button>
                )}
              </div>
              <McpServerForm
                server={editing}
                isSaving={create.isPending || update.isPending}
                error={
                  create.isError
                    ? errorText(create.error)
                    : update.isError
                      ? errorText(update.error)
                      : null
                }
                onSubmit={(body) =>
                  adding
                    ? create.mutate(body)
                    : selected && update.mutate({ id: selected.id, body })
                }
                onCancel={() => {
                  setAdding(false);
                  setSelectedId(null);
                }}
              />
            </>
          ) : (
            <>
              <div className="state-label">Recent tool calls</div>
              <McpActivityFeed />
            </>
          )}
        </section>
      </div>
    </div>
  );
}
