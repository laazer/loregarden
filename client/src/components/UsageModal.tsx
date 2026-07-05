import type { UsageBreakdownItem, UsageMeter, UsageProviderSnapshot, UsageSnapshot } from "../api/client";

interface UsageModalProps {
  open: boolean;
  snapshot: UsageSnapshot | undefined;
  isLoading: boolean;
  error: Error | null;
  onClose: () => void;
  onRefresh: () => void;
}

function formatMeterValue(meter: UsageMeter): string {
  if (meter.unit === "percent") {
    return `${meter.used.toFixed(0)}%`;
  }
  if (meter.unit === "dollars") {
    const used = `$${meter.used.toFixed(2)}`;
    return meter.limit != null ? `${used} / $${meter.limit.toFixed(2)}` : used;
  }
  return `${meter.used.toFixed(0)}${meter.limit != null ? ` / ${meter.limit.toFixed(0)}` : ""}`;
}

function meterBarColor(status: UsageMeter["status"]): string {
  if (status === "critical") return "var(--red)";
  if (status === "warning") return "var(--amb)";
  return "var(--grn)";
}

function providerTitle(provider: UsageProviderSnapshot["provider"]): string {
  return provider === "claude" ? "Claude" : "Cursor";
}

function breakdownLabel(item: UsageBreakdownItem): string {
  if (item.unit === "tokens") return `${Math.round(item.amount).toLocaleString()} tokens`;
  if (item.unit === "events") return `${Math.round(item.amount).toLocaleString()} events`;
  return `${item.amount.toFixed(1)} ${item.unit}`;
}

function ProviderSection({
  provider,
}: {
  provider: UsageProviderSnapshot;
}) {
  return (
    <section className="usage-provider-section">
      <div className="usage-provider-header">
        <div>
          <div className="state-label">{providerTitle(provider.provider)}</div>
          <h3 className="usage-provider-title">
            {provider.plan ?? (provider.logged_in ? "Signed in" : "Not signed in")}
          </h3>
        </div>
        {provider.error && <span className="usage-provider-error">{provider.error}</span>}
      </div>

      {provider.meters.length > 0 ? (
        <div className="usage-meter-list">
          {provider.meters.map((meter) => {
            const percent =
              meter.percent_used ??
              (meter.limit && meter.limit > 0 ? (meter.used / meter.limit) * 100 : meter.used);
            const width = Math.max(4, Math.min(100, percent));
            return (
              <div key={meter.key} className="usage-meter-row">
                <div className="usage-meter-top">
                  <span>{meter.label}</span>
                  <span className="usage-meter-value">{formatMeterValue(meter)}</span>
                </div>
                <div className="usage-meter-track">
                  <div
                    className="usage-meter-fill"
                    style={{ width: `${width}%`, background: meterBarColor(meter.status) }}
                  />
                </div>
                {meter.resets_at && (
                  <div className="usage-meter-reset">Resets {new Date(meter.resets_at).toLocaleString()}</div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        !provider.error && <p className="modal-hint">No live usage meters returned.</p>
      )}

      {provider.breakdown.length > 0 && (
        <div className="usage-breakdown">
          <div className="usage-breakdown-title">Top consumers (last 7 days)</div>
          {provider.breakdown.map((item) => (
            <div key={`${provider.provider}-${item.name}`} className="usage-breakdown-row">
              <div className="usage-breakdown-name">{item.name}</div>
              <div className="usage-breakdown-meta">
                <span>{breakdownLabel(item)}</span>
                <span>{item.share_percent.toFixed(0)}%</span>
              </div>
              <div className="usage-meter-track">
                <div
                  className="usage-meter-fill"
                  style={{ width: `${Math.max(4, item.share_percent)}%`, background: "var(--acc)" }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export function UsageModal({ open, snapshot, isLoading, error, onClose, onRefresh }: UsageModalProps) {
  if (!open) return null;

  return (
    <>
      <div className="modal-overlay" onClick={isLoading ? undefined : onClose} role="presentation" />
      <div className="modal-panel usage-modal-panel" role="dialog" aria-labelledby="usage-modal-title">
        <div className="modal-header">
          <div>
            <div className="state-label">Providers</div>
            <h2 id="usage-modal-title" className="modal-title">
              Claude &amp; Cursor usage
            </h2>
            <p className="modal-subtitle">
              Live subscription limits plus local activity breakdown
              {snapshot?.fetched_at ? ` · updated ${new Date(snapshot.fetched_at).toLocaleTimeString()}` : ""}
            </p>
          </div>
          <button type="button" className="btn-secondary" disabled={isLoading} onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-body usage-modal-body">
          {isLoading && !snapshot && <p className="modal-hint">Loading usage…</p>}
          {error && <p className="usage-provider-error">{error.message}</p>}
          {snapshot?.warnings.length ? (
            <div className="usage-warning-banner">
              {snapshot.warnings.map((warning) => (
                <div key={warning}>! {warning}</div>
              ))}
            </div>
          ) : null}
          {snapshot?.providers.map((provider) => (
            <ProviderSection key={provider.provider} provider={provider} />
          ))}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isLoading} onClick={onClose}>
            Close
          </button>
          <button type="button" className="btn-primary" disabled={isLoading} onClick={onRefresh}>
            {isLoading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>
    </>
  );
}
