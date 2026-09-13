// What the SPA says about itself, and how loudly.
//
// The browser is the only place this half of the stack leaves a trace. Both
// servers bind every interface (docs/adr/0016, 0022), so the page is as often
// opened from another machine as from the one running `make run` — and "look
// at the terminal" is then no answer at all when a screen stays empty. See
// docs/adr/0021.
//
// It is `console` and not a library on purpose: a logging dependency in a SPA
// buys buffering, transports and levels, and we need one of the three.

export type LogLevel = 'debug' | 'info' | 'warn' | 'error' | 'silent'

/** Ordered, so a level is a threshold rather than a set of names to compare. */
const RANK: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
  silent: 100,
}

const CONSOLE: Record<Exclude<LogLevel, 'silent'>, (...args: unknown[]) => void> = {
  debug: (...args) => console.debug(...args),
  info: (...args) => console.info(...args),
  warn: (...args) => console.warn(...args),
  error: (...args) => console.error(...args),
}

function isLevel(value: string): value is LogLevel {
  return value in RANK
}

/**
 * The level to speak at: the one configured, else everything in dev and only
 * failures in a built bundle.
 *
 * A developer running `npm run dev` is the person who wants every call
 * printed; a user on a deployed build wants a console that is quiet until
 * something breaks. `VITE_LOG_LEVEL` overrides either.
 */
export function resolveLevel(configured: string | undefined, dev: boolean): LogLevel {
  const wanted = (configured ?? '').trim().toLowerCase()
  if (wanted && isLevel(wanted)) {
    return wanted
  }
  return dev ? 'debug' : 'warn'
}

export const LOG_LEVEL: LogLevel = resolveLevel(
  import.meta.env.VITE_LOG_LEVEL as string | undefined,
  import.meta.env.DEV,
)

export type Logger = Record<Exclude<LogLevel, 'silent'>, (message: string, fields?: object) => void>

/**
 * A logger for one part of the SPA, printing `[scope] message` and the fields.
 *
 * The fields stay an object rather than being interpolated into the string:
 * a browser console renders one as an expandable value, and a request id
 * flattened into prose is a request id nobody can copy.
 */
export function createLogger(scope: string, level: LogLevel = LOG_LEVEL): Logger {
  const speak = (at: Exclude<LogLevel, 'silent'>) => (message: string, fields?: object) => {
    if (RANK[at] < RANK[level]) {
      return
    }
    if (fields === undefined) {
      CONSOLE[at](`[${scope}] ${message}`)
    } else {
      CONSOLE[at](`[${scope}] ${message}`, fields)
    }
  }
  return { debug: speak('debug'), info: speak('info'), warn: speak('warn'), error: speak('error') }
}
