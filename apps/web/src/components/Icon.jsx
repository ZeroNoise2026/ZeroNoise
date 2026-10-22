/**
 * Minimal stroke icons (Feather-style, 1.5px). Single-color via currentColor.
 * Keeping all glyphs in one file so the bundle stays tiny and we can audit
 * the visual language at a glance.
 */

const STROKE = {
    width: 24,
    height: 24,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.6,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
}

export function Icon({ name, size = 16, className, title }) {
    const path = PATHS[name]
    if (!path) return null
    return (
        <svg
            {...STROKE}
            width={size}
            height={size}
            className={className}
            aria-hidden={title ? undefined : 'true'}
            role={title ? 'img' : undefined}
        >
            {title && <title>{title}</title>}
            {path}
        </svg>
    )
}

const PATHS = {
    briefing: (
        <>
            <rect x="4" y="3" width="16" height="18" rx="2" />
            <path d="M8 8h8M8 12h8M8 16h5" />
        </>
    ),
    star: (
        <path d="M12 3l2.6 5.3 5.9.8-4.3 4.1 1 5.8L12 16.9 6.8 19l1-5.8L3.5 9.1l5.9-.8L12 3z" />
    ),
    chat: (
        <path d="M4 5h16v11H8l-4 4V5z" />
    ),
    plus: (
        <path d="M12 5v14M5 12h14" />
    ),
    chevronLeft: <path d="M14 6l-6 6 6 6" />,
    chevronRight: <path d="M10 6l6 6-6 6" />,
    chevronsLeft: <><path d="M11 6l-6 6 6 6" /><path d="M19 6l-6 6 6 6" /></>,
    chevronsRight: <><path d="M5 6l6 6-6 6" /><path d="M13 6l6 6-6 6" /></>,
    close: <path d="M6 6l12 12M18 6L6 18" />,
    paperclip: (
        <path d="M21 11l-9 9a5 5 0 11-7-7L14 4a3.5 3.5 0 014.95 4.95L10 18a2 2 0 11-2.83-2.83L15 7.5" />
    ),
    arrowUp: <path d="M12 19V5M5 12l7-7 7 7" />,
    file: (
        <>
            <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5z" />
            <path d="M14 3v5h5" />
        </>
    ),
    fileText: (
        <>
            <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5z" />
            <path d="M14 3v5h5M9 13h6M9 17h6M9 9h2" />
        </>
    ),
    fileSpreadsheet: (
        <>
            <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5z" />
            <path d="M14 3v5h5M8 13h8M8 17h8M12 13v8" />
        </>
    ),
    folder: <path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />,
    user: (
        <>
            <circle cx="12" cy="8" r="4" />
            <path d="M4 21v-1a6 6 0 016-6h4a6 6 0 016 6v1" />
        </>
    ),
    signOut: (
        <>
            <path d="M15 4h3a2 2 0 012 2v12a2 2 0 01-2 2h-3" />
            <path d="M10 17l-5-5 5-5M5 12h11" />
        </>
    ),
    trash: (
        <>
            <path d="M4 7h16" />
            <path d="M10 11v6M14 11v6" />
            <path d="M5 7l1 13a2 2 0 002 2h8a2 2 0 002-2l1-13" />
            <path d="M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3" />
        </>
    ),
    sparkle: (
        <>
            <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8" />
        </>
    ),
}
