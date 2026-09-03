// Transport hook for the ViVeSec embedding: when the AIBox UI is loaded inside
// the ViVeSecBox iframe, the embedding index.html defines this global before the
// app bundle runs, and every server-function call is tunnelled through it.
declare global {
  // eslint-disable-next-line no-var
  var frameSocketFetcher: typeof fetch | undefined;
}

export {};
