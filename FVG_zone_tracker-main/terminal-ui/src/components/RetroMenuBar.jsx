const MENUS = ['File', 'Edit', 'View', 'Terminal', 'Tabs', 'Help']

export default function RetroMenuBar() {
  return (
    <div className="flex items-center h-6 bg-term-menubar border-b border-term-border px-1 flex-shrink-0 select-none">
      {MENUS.map(m => (
        <button
          key={m}
          className="px-2 py-0 text-2xs text-term-muted hover:text-term-text hover:bg-term-border transition-colors leading-6 border-0 bg-transparent cursor-default"
        >
          {m}
        </button>
      ))}
      <div className="ml-auto flex items-center gap-3 pr-2">
        <span className="text-3xs text-term-muted tracking-wider">FVG-TERMINAL v1.0</span>
        <span className="text-3xs text-term-muted">●</span>
        <span className="text-3xs text-term-green opacity-60">CONNECTED</span>
      </div>
    </div>
  )
}
