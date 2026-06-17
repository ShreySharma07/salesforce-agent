// components/ConnectorBand.tsx
// The signature element: a glass rail along which the apps this agent can
// drive drift continuously — a literal depiction of "plugs into your stack."
// Pure CSS marquee (two copies, translateX) so it loops seamlessly and
// respects reduced-motion.

"use client";

const CONNECTORS = [
  "Salesforce",
  "Gmail",
  "Slack",
  "HubSpot",
  "Zendesk",
  "Notion",
  "Jira",
  "Google Drive",
  "Asana",
  "Stripe",
];

function Row() {
  return (
    <div className="band-row">
      {CONNECTORS.map((name) => (
        <div key={name} className="band-chip glass">
          <span className="band-dot" />
          <span className="mono">{name}</span>
        </div>
      ))}
    </div>
  );
}

export function ConnectorBand() {
  return (
    <div className="band" aria-label="Apps this agent can operate">
      <div className="band-track">
        <Row />
        <Row />
      </div>

      <style jsx>{`
        .band {
          position: relative;
          overflow: hidden;
          padding: 14px 0;
          mask-image: linear-gradient(
            to right,
            transparent,
            black 8%,
            black 92%,
            transparent
          );
        }
        .band-track {
          display: flex;
          width: max-content;
          animation: drift 32s linear infinite;
        }
        .band-row {
          display: flex;
          gap: 14px;
          padding-right: 14px;
        }
        .band-chip {
          display: flex;
          align-items: center;
          gap: 9px;
          padding: 10px 18px;
          white-space: nowrap;
          font-size: 14px;
          color: var(--text-dim);
        }
        .band-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: var(--accent);
          opacity: 0.8;
        }
        @keyframes drift {
          from {
            transform: translateX(0);
          }
          to {
            transform: translateX(-50%);
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .band-track {
            animation: none;
          }
        }
      `}</style>
    </div>
  );
}