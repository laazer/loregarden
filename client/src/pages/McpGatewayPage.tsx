import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, type McpServerInput, type McpServerView } from "../api/client";
import { McpHealthBadge } from "../components/mcp/McpHealthBadge";
import {
  gatewayMetrics,
  routingRules,
  switchboardAgents,
  switchboardServers,
  LOREGARDEN_SERVER,
} from "../components/mcp/mcpRouting";
import { McpRoutingRules } from "../components/mcp/McpRoutingRules";
import { McpServerDetail } from "../components/mcp/McpServerDetail";
import { McpServerModal } from "../components/mcp/McpServerModal";
import { McpSwitchboard } from "../components/mcp/McpSwitchboard";
import { PageTopbar } from "../components/TopbarPageSlot";
import "./McpGatewayPage.css";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}

function ServerRow({
  server,
  selected,
  toolLabel,
  callRate,
  onSelect,
}: {
  server: McpServerView;
  selected: boolean;
  toolLabel: string;
  callRate: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={`mcp-server-row${selected ? " selected" : ""}`}
      aria-label={`${server.name} — ${server.transport}, ${toolLabel}`}
      aria-pressed={selected}
      onClick={onSelect}
    >
      <div className="mcp-server-row-head">
        <span
          className={`mcp-node-dot mcp-node-dot--${
            !server.last_checked_at ? "unknown" : server.last_health_ok ? "ok" : "bad"
          }`}
        />
        <span className="mcp-server-name">{server.name}</span>
        <span className="mcp-transport-pill">{server.transport}</span>
      </div>
      <div className="mcp-server-row-stats">
        <span>{toolLabel}</span>
        <span className="mcp-server-row-rate">{callRate}</span>
      </div>
      <div className="mcp-server-row-meta">
        {server.transport === "http" ? server.url : server.command}
      </div>
      {!server.enabled && <span className="state-label">withheld</span>}
      {server.enabled && server.tool_policy === "auto" && (
        <span className="state-label mcp-server-trusted">trusted</span>
      )}
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
 * The MCP servers agents can reach, who reaches them, and what they were asked.
 *
 * Three panes, matching how the question is actually asked: the registry on the
 * left is what exists, the switchboard and rules in the middle are what routes
 * where, and the pane on the right is one server in full.
 *
 * Every number here is measured rather than modelled. The tool counts come from
 * a real `tools/list` during a health check, so a server nobody has checked
 * reads as "not listed" rather than as zero. The rate is permission *decisions*
 * per minute over a stated window — the bridge never sees a tool execute, so
 * there is still no execution latency or success rate anywhere on this page.
 */
export function McpGatewayPage() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState<McpServerView | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const servers = useQuery({ queryKey: ["mcp-servers"], queryFn: api.mcpServers });
  const telemetry = useQuery({
    queryKey: ["mcp-telemetry"],
    queryFn: api.mcpTelemetry,
    refetchInterval: 5000,
  });
  const agents = useQuery({ queryKey: ["studio-agents"], queryFn: api.studioAgents });
  // The auto-approve set lives in the server's policy module; mirroring it here
  // would drift and then misreport what runs unattended.
  const policy = useQuery({ queryKey: ["mcp-policy"], queryFn: api.mcpPolicy, staleTime: Infinity });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["mcp-servers"] });

  const closeModal = () => {
    setModalOpen(false);
    setEditing(null);
  };

  const create = useMutation({
    meta: { errorTitle: "Add MCP server" },
    mutationFn: (body: McpServerInput) => api.createMcpServer(body),
    onSuccess: (created) => {
      invalidate();
      closeModal();
      setSelectedId(created.id);
    },
  });

  const update = useMutation({
    meta: { errorTitle: "Update MCP server" },
    mutationFn: ({ id, body }: { id: string; body: Partial<McpServerInput> }) =>
      api.updateMcpServer(id, body),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
  });

  const checkHealth = useMutation({
    meta: { errorTitle: "Check MCP server health" },
    mutationFn: (id: string) => api.checkMcpServerHealth(id),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    meta: { errorTitle: "Remove MCP server" },
    mutationFn: (id: string) => api.deleteMcpServer(id),
    onSuccess: () => {
      invalidate();
      setSelectedId(null);
    },
  });

  const rows = useMemo(() => servers.data ?? [], [servers.data]);
  const agentRows = useMemo(() => agents.data ?? [], [agents.data]);
  const selected = rows.find((s) => s.id === selectedId) ?? null;

  const metrics = useMemo(
    () => gatewayMetrics(rows, agentRows, telemetry.data),
    [rows, agentRows, telemetry.data],
  );
  const rules = useMemo(
    () => routingRules(rows, agentRows, policy.data),
    [rows, agentRows, policy.data],
  );
  const boardAgents = useMemo(() => switchboardAgents(agentRows), [agentRows]);
  const boardServers = useMemo(() => switchboardServers(rows), [rows]);

  const activityFor = (name: string) => telemetry.data?.per_server?.[name];
  const rateLabel = (name: string) => {
    const activity = activityFor(name);
    return activity ? `${activity.calls_per_min}/min` : "no calls";
  };
  const toolLabel = (server: McpServerView) =>
    server.tools_listed_at ? `${server.tools.length} tools` : "tools not listed";

  return (
    <div className="screen-view screen-view--mcp">
      <PageTopbar title="MCP Gateway">
        <span className="topbar-page-note">
          Register a server once — it joins every agent&rsquo;s config at start
        </span>
        <button
          type="button"
          className="btn-primary topbar-page-btn"
          onClick={() => {
            setEditing(null);
            setModalOpen(true);
          }}
        >
          Register server
        </button>
      </PageTopbar>

      <div className="mcp-page-body">
        <aside className="mcp-servers-rail" data-testid="mcp-registry-rail">
          <div className="state-label">Registered servers</div>

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
              toolLabel={toolLabel(server)}
              callRate={rateLabel(server.name)}
              onSelect={() => setSelectedId(server.id)}
            />
          ))}

          <div className="state-label mcp-builtin-label">Always available</div>
          <div className="mcp-server-row builtin">
            <div className="mcp-server-row-head">
              <span className="mcp-node-dot mcp-node-dot--ok" />
              <span className="mcp-server-name">{LOREGARDEN_SERVER}</span>
              <span className="mcp-transport-pill">built in</span>
            </div>
            <div className="mcp-server-row-stats">
              <span>{rateLabel(LOREGARDEN_SERVER)}</span>
            </div>
            <div className="mcp-server-row-meta">
              This control plane&rsquo;s own tools — tickets, artifacts, memory.
            </div>
          </div>
        </aside>

        <main className="mcp-main">
          <div className="mcp-metrics">
            {metrics.map((metric) => (
              <div key={metric.label} className={`mcp-metric mcp-metric--${metric.tone}`} title={metric.title}>
                <div className="mcp-metric-label">{metric.label}</div>
                <div className="mcp-metric-value">{metric.value}</div>
              </div>
            ))}
          </div>

          <div className="mcp-section-head">
            <span className="mcp-section-title">Routing switchboard</span>
            <span className="mcp-section-note">
              every MCP-enabled agent reaches every enabled server — there is no per-agent grant
            </span>
          </div>
          <McpSwitchboard
            agents={boardAgents}
            servers={boardServers}
            selectedServer={selected?.id ?? null}
            onSelectServer={(key) => setSelectedId(key === LOREGARDEN_SERVER ? null : key)}
          />

          <div className="mcp-section-head">
            <span className="mcp-section-title">Routing rules</span>
            {/* The table scrolls, so the count is what says how much is below
                the fold rather than leaving the operator to guess. */}
            {rules.length > 0 && <span className="count-pill">{rules.length}</span>}
            <span className="mcp-section-note">what each agent may call, and what stops for you</span>
          </div>
          <McpRoutingRules rules={rules} />
        </main>

        <aside className="mcp-detail" data-testid="mcp-server-detail">
          {selected ? (
            <McpServerDetail
              server={selected}
              activity={activityFor(selected.name)}
              isChecking={checkHealth.isPending}
              isRemoving={remove.isPending}
              onCheckHealth={() => checkHealth.mutate(selected.id)}
              onEdit={() => {
                setEditing(selected);
                setModalOpen(true);
              }}
              onRemove={() => remove.mutate(selected.id)}
            />
          ) : (
            <div className="mcp-detail-pane">
              <div className="mcp-detail-title-row">
                <span className="mcp-server-name">{LOREGARDEN_SERVER}</span>
                <span className="state-label">built in</span>
              </div>
              <p className="mcp-detail-desc">
                This control plane&rsquo;s own server — tickets, artifacts, memory and
                approvals. Reached in process, so there is nothing to health-check and nothing
                to register.
              </p>
              <div className="state-label">Pick a server to see it in full</div>
              {rows.length > 0 && (
                <div className="mcp-empty">
                  Select one on the left, or a node on the switchboard.
                </div>
              )}
              <div className="state-label">Registry health</div>
              <div className="mcp-registry-health">
                {rows.map((server) => (
                  <div key={server.id} className="mcp-registry-health-row">
                    <span className="mcp-server-name">{server.name}</span>
                    <McpHealthBadge server={server} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </aside>
      </div>

      <McpServerModal
        open={modalOpen}
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
          editing ? update.mutate({ id: editing.id, body }) : create.mutate(body)
        }
        onClose={closeModal}
      />
    </div>
  );
}
