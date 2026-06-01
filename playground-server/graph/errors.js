/** @param {string} msg @param {{ code?: string }} [opts] */
export function jsonError(msg, { code = 'bad_request' } = {}) {
  return { error: msg, code };
}
