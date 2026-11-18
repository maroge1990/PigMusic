/**
 * The writes the server takes before anyone signs in: activating an invite
 * or a password reset, signing in (web and device) and creating the first
 * owner. They name no session; every other write does (#250).
 * tests/test_public_writes.py asks the server for every write it takes
 * signed out and fails when this list differs.
 */
export const PUBLIC_WRITES: ReadonlySet<string> = new Set([
  "POST /api/v1/auth/device",
  "POST /api/v1/auth/login",
  "POST /api/v1/setup",
  "POST /api/v1/setup/owner",
]);
