interface TopbarProps {
  title: string;
  sub?: string;
}

export default function Topbar({ title, sub }: TopbarProps) {
  return (
    <header
      style={{
        background: '#ffffff',
        borderBottom: '1px solid #ededed',
        padding: '18px 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexShrink: 0,
      }}
    >
      {/* Left: Title + Sub */}
      <div>
        <h1
          style={{
            margin: 0,
            fontSize: 20,
            fontWeight: 600,
            color: '#111',
            letterSpacing: '-0.3px',
            lineHeight: 1.2,
          }}
        >
          {title}
        </h1>
        {sub && (
          <p style={{ margin: '2px 0 0', fontSize: 13, color: '#737373', lineHeight: 1.4 }}>
            {sub}
          </p>
        )}
      </div>

      {/* Right: Search + Status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        {/* Search box */}
        <div
          style={{
            width: 280,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            background: '#f5f5f5',
            border: '1px solid #ededed',
            borderRadius: 6,
            padding: '6px 10px',
          }}
        >
          <span style={{ fontSize: 13, color: '#a3a3a3', flex: 1 }}>Suche oder Befehl…</span>
          <kbd
            style={{
              background: '#ffffff',
              border: '1px solid #ededed',
              borderRadius: 4,
              padding: '1px 5px',
              fontSize: 11,
              color: '#737373',
              fontFamily: 'inherit',
            }}
          >
            ⌘K
          </kbd>
        </div>

        {/* Status Pill */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '5px 10px',
            background: '#ecfdf5',
            border: '1px solid #d1fae5',
            borderRadius: 14,
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: '#10b981',
              flexShrink: 0,
            }}
          />
          <span style={{ fontSize: 12, color: '#047857', fontWeight: 500, whiteSpace: 'nowrap' }}>
            Alle Dienste betriebsbereit
          </span>
        </div>
      </div>
    </header>
  );
}
