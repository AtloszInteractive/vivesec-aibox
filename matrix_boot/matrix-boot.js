/*
 * Matrix AI bootloader (ViVeSec AI Box, E07).
 *
 * The ViVeSec frame socket carries only the requests this page makes itself
 * (globalThis.frameSocketFetcher), not the browser's own script, style or image
 * loads. So this small script fetches the whole UI from the AI Box in one piece
 * and puts it into the page:
 *
 *   1. GET /api/v1/matrix/app/manifest           -> label, sha256 of the bundle
 *   2. the bundle from IndexedDB when its sha256 is the manifest's, otherwise
 *      GET /api/v1/matrix/app/bundle/<sha256>    -> {html, script}, checked
 *   3. the prerendered shell (html) goes into this document: its inline
 *      scripts are run, the module entry it names stays inert, and every
 *      asset in it is a data: URL, so nothing is fetched from the host page
 *   4. the client script runs as an inline module (a blob: URL when the page's
 *      Content-Security-Policy refuses inline scripts) and hydrates the shell
 *
 * globalThis.matrixApiPrefix tells the UI to call /api/v1/matrix/... instead of
 * /api/v1/ui/... (the Dokumentum AI owns that prefix on the same ViVeSecBox).
 */
(function () {
  "use strict";

  var PREFIX = "/api/v1/matrix/";
  var TRANSPORT_WAIT_MS = 10000;
  var CHANNEL_WAIT_MS = 20000;
  var CHANNEL_RETRY_MS = 250;
  var START_WAIT_MS = 4000;
  var DB_NAME = "vivesec-matrix";
  var DB_STORE = "app";

  var TEXT = {
    en: {
      loading: "Loading Matrix AI…",
      failed: "Matrix AI could not be loaded.",
      noTransport: "This page works only inside the ViVeSec app.",
      retry: "Try again",
    },
    hu: {
      loading: "A Matrix AI betöltése…",
      failed: "A Matrix AI nem tölthető be.",
      noTransport: "Ez az oldal csak a ViVeSec alkalmazáson belül működik.",
      retry: "Újrapróbálás",
    },
    da: {
      loading: "Indlæser Matrix AI…",
      failed: "Matrix AI kunne ikke indlæses.",
      noTransport: "Denne side virker kun i ViVeSec-appen.",
      retry: "Prøv igen",
    },
    de: {
      loading: "Matrix AI wird geladen…",
      failed: "Matrix AI konnte nicht geladen werden.",
      noTransport: "Diese Seite funktioniert nur in der ViVeSec-App.",
      retry: "Erneut versuchen",
    },
  };

  function language() {
    var wanted = [];
    try {
      wanted.push(window.localStorage.getItem("vivesec_lang") || "");
    } catch (e) {
      /* storage may be blocked in a third-party frame */
    }
    wanted = wanted.concat(navigator.languages || [navigator.language || ""]);
    for (var i = 0; i < wanted.length; i++) {
      var code = String(wanted[i] || "")
        .slice(0, 2)
        .toLowerCase();
      if (TEXT[code]) return code;
    }
    return "en";
  }

  var text = TEXT[language()];
  var box = document.getElementById("matrix-boot");

  function show(key, detail) {
    if (!box) return;
    box.querySelector(".message").textContent = text[key];
    box.querySelector(".detail").textContent = detail || "";
    var button = box.querySelector("button");
    button.textContent = text.retry;
    if (key !== "loading") box.className = "failed";
  }

  if (box) {
    show("loading");
    box.querySelector("button").addEventListener("click", function () {
      window.location.reload();
    });
  }

  function transport() {
    return new Promise(function (resolve, reject) {
      var started = Date.now();
      (function poll() {
        if (typeof globalThis.frameSocketFetcher === "function") {
          resolve(globalThis.frameSocketFetcher);
        } else if (Date.now() - started > TRANSPORT_WAIT_MS) {
          reject(Object.assign(new Error("no frameSocketFetcher"), { key: "noTransport" }));
        } else {
          setTimeout(poll, 50);
        }
      })();
    });
  }

  function bytesOf(response) {
    if (typeof response.arrayBuffer === "function") {
      return response.arrayBuffer().then(function (buffer) {
        return new Uint8Array(buffer);
      });
    }
    return response.text().then(function (body) {
      return new TextEncoder().encode(body);
    });
  }

  function get(fetcher, path) {
    return call(fetcher, path, Date.now()).then(function (response) {
      if (!response.ok) throw new Error(path + ": HTTP " + response.status);
      return bytesOf(response);
    });
  }

  // The frame socket function exists before its channel to the parent page is
  // up ("No active ParentFrameSocket"): a call that fails without an HTTP answer
  // is tried again for a while. An HTTP error is an answer and is not retried.
  function call(fetcher, path, started) {
    return Promise.resolve()
      .then(function () {
        return fetcher(PREFIX + path, { method: "GET", headers: { Accept: "application/json" } });
      })
      .catch(function (error) {
        if (Date.now() - started > CHANNEL_WAIT_MS) throw error;
        return new Promise(function (resolve) {
          setTimeout(resolve, CHANNEL_RETRY_MS);
        }).then(function () {
          return call(fetcher, path, started);
        });
      });
  }

  function sha256(bytes) {
    return crypto.subtle.digest("SHA-256", bytes).then(function (digest) {
      return Array.prototype.map
        .call(new Uint8Array(digest), function (byte) {
          return ("0" + byte.toString(16)).slice(-2);
        })
        .join("");
    });
  }

  // The bundle is a few megabytes; IndexedDB keeps the last one by its digest.
  // Every failure here only means a download.
  function database() {
    return new Promise(function (resolve) {
      try {
        var request = indexedDB.open(DB_NAME, 1);
        request.onupgradeneeded = function () {
          request.result.createObjectStore(DB_STORE);
        };
        request.onsuccess = function () {
          resolve(request.result);
        };
        request.onerror = function () {
          resolve(null);
        };
      } catch (e) {
        resolve(null);
      }
    });
  }

  function cached(digest) {
    return database().then(function (db) {
      if (!db) return null;
      return new Promise(function (resolve) {
        try {
          var request = db.transaction(DB_STORE).objectStore(DB_STORE).get("bundle");
          request.onsuccess = function () {
            var hit = request.result;
            resolve(hit && hit.sha256 === digest ? hit.bytes : null);
          };
          request.onerror = function () {
            resolve(null);
          };
        } catch (e) {
          resolve(null);
        }
      });
    });
  }

  function remember(digest, bytes) {
    database().then(function (db) {
      if (!db) return;
      try {
        db.transaction(DB_STORE, "readwrite")
          .objectStore(DB_STORE)
          .put({ sha256: digest, bytes: bytes }, "bundle");
      } catch (e) {
        /* not cached: the next start downloads it again */
      }
    });
  }

  function bundle(fetcher, digest) {
    return cached(digest)
      .then(function (hit) {
        return hit
          ? sha256(hit).then(function (actual) {
              return actual === digest ? hit : null;
            })
          : null;
      })
      .then(function (hit) {
        if (hit) return hit;
        return get(fetcher, "app/bundle/" + digest).then(function (bytes) {
          return sha256(bytes).then(function (actual) {
            if (actual !== digest) throw new Error("bundle digest mismatch");
            remember(digest, bytes);
            return bytes;
          });
        });
      });
  }

  function copyAttributes(from, to) {
    for (var i = 0; i < from.attributes.length; i++) {
      to.setAttribute(from.attributes[i].name, from.attributes[i].value);
    }
  }

  // A script node from the parsed shell is never inserted as it is: an inline
  // one is replaced by a fresh element (which runs when it is connected), and
  // one with a src stays in the page for hydration but can never load.
  function prepare(node) {
    var scripts = node.nodeName === "SCRIPT" ? [node] : [];
    if (node.querySelectorAll)
      scripts = scripts.concat([].slice.call(node.querySelectorAll("script")));
    var root = node;
    scripts.forEach(function (script) {
      if (script.hasAttribute("src")) {
        script.setAttribute("type", "matrix/inert");
        return;
      }
      var fresh = document.createElement("script");
      copyAttributes(script, fresh);
      fresh.textContent = script.textContent;
      if (script === root) root = fresh;
      else script.parentNode.replaceChild(fresh, script);
    });
    return root;
  }

  function mount(app) {
    var shell = new DOMParser().parseFromString(app.html, "text/html");
    [].slice.call(document.querySelectorAll("[data-matrix-boot]")).forEach(function (node) {
      node.parentNode.removeChild(node);
    });
    copyAttributes(shell.documentElement, document.documentElement);
    copyAttributes(shell.body, document.body);
    [].slice.call(shell.head.childNodes).forEach(function (node) {
      document.head.appendChild(prepare(document.importNode(node, true)));
    });
    var anchor = document.body.firstChild;
    [].slice.call(shell.body.childNodes).forEach(function (node) {
      document.body.insertBefore(prepare(document.importNode(node, true)), anchor);
    });
    globalThis.matrixApiPrefix = PREFIX;
    run(app.script);
  }

  function run(source) {
    var refused = false;
    var onViolation = function (event) {
      if (event.violatedDirective && event.violatedDirective.indexOf("script-src") === 0)
        refused = true;
    };
    document.addEventListener("securitypolicyviolation", onViolation);
    var inline = document.createElement("script");
    inline.type = "module";
    inline.textContent = source;
    document.body.appendChild(inline);
    setTimeout(function () {
      document.removeEventListener("securitypolicyviolation", onViolation);
      if (globalThis.matrixAppStarted || !refused) return;
      var blob = document.createElement("script");
      blob.type = "module";
      blob.src = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
      document.body.appendChild(blob);
    }, START_WAIT_MS);
  }

  function start() {
    return transport().then(function (fetcher) {
      return get(fetcher, "app/manifest")
        .then(function (bytes) {
          var manifest = JSON.parse(new TextDecoder().decode(bytes));
          if (manifest.format !== 1 || !manifest.bundle || !manifest.bundle.sha256) {
            throw new Error("unknown manifest");
          }
          return bundle(fetcher, manifest.bundle.sha256);
        })
        .then(function (bytes) {
          var app = JSON.parse(new TextDecoder().decode(bytes));
          if (app.format !== 1 || typeof app.html !== "string" || typeof app.script !== "string") {
            throw new Error("unknown bundle");
          }
          mount(app);
        });
    });
  }

  start().catch(function (error) {
    console.error("[matrix-boot]", error);
    show(error && error.key ? error.key : "failed", error && error.message);
  });
})();
