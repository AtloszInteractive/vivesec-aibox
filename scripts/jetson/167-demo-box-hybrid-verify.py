import json
import time
import urllib.error
import urllib.request


BASE = "http://127.0.0.1:8080/api/v1"


def post(path, payload):
    request = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def ask(question):
    started = time.monotonic()
    code, submission = post("/ui/ask", {
        "query": question, "profile": "hybrid", "lang": "Hungarian", "origin": "chat",
    })
    assert code == 202, (code, submission)
    print("JOB", submission["job_id"], flush=True)
    deadline = started + 600
    while time.monotonic() < deadline:
        code, result = post("/ui/poll", {"job_id": submission["job_id"], "timeout": 25})
        assert code == 200, (code, result)
        if result.get("status") == "pending":
            continue
        print(json.dumps({
            "question": question, "seconds": round(time.monotonic() - started, 1),
            "profile": result.get("profile"), "backend": result.get("backend"),
            "confidence": result.get("confidence"), "audit_id": result.get("audit_id"),
            "answer": result.get("answer"), "citations": len(result.get("citations") or []),
        }, ensure_ascii=False), flush=True)
        assert result.get("status") == "done", result
        assert result.get("profile") == "hybrid", result
        assert result.get("confidence") is None, result
        assert "hybrid" in (result.get("backend") or ""), result
        return
    raise TimeoutError("Job still running: " + submission["job_id"])


def main():
    code, status = post("/status", {})
    print("POLICY", json.dumps(status.get("chat_policy")), flush=True)
    assert code == 200 and status.get("chat_policy") == {
        "policy": "locked_hybrid", "default_profile": "hybrid", "allow_switch": False,
    }, status
    code, result = post("/ui/ask", {"query": "Profile lock check", "profile": "grounded"})
    assert code == 403, (code, result)
    print("PASS: profile switching rejected", flush=True)
    ask("Magyarul, roviden magyarazd el, mi a kulonbseg a bevetel es a profit kozott. "
        "Altalanos penzugyi ismeretet kerek, nem a ceg sajat adatait.")
    ask("Irj egy baratsagos, harommondatos meghivot egy kollegaknak szolo kozos ebedre. "
        "Ez egy fiktiv szovegtervezet; az idopontot es helyszint hagyd kitoltendo mezokent.")
    ask("Mennyi a cegunk 2031. novemberi pontos netto profitja? "
        "Csak ellenorzott ceges adatot adj, ne becsulj es ne talalj ki szamot.")
    code, result = post("/ui/query", {
        "query": "files:README", "action": "search", "mode": "files", "profile": "hybrid",
    })
    assert code == 200 and result.get("profile") == "grounded", (code, result)
    print("PASS: quick action remains grounded", flush=True)


if __name__ == "__main__":
    main()