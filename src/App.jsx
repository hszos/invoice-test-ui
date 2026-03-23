import { useState } from "react";

export default function App() {
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState("");

  const analyze = async () => {
    if (!file) {
      setStatus("Bitte eine PDF-Datei auswählen.");
      return;
    }

    setStatus("Sende an Azure Function ...");

    try {
      const response = await fetch(
        "https://<YOUR-FUNCTION>.azurewebsites.net/api/<YOUR-FUNCTION-NAME>?code=<YOUR-FUNCTION-KEY>",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/pdf",
            "X-PDF-Filename": file.name
          },
          body: file
        }
      );

      const text = await response.text();

      if (response.ok) {
        setStatus("✅ Erfolgreich! Antwort der Function:\n" + text);
      } else {
        setStatus("❌ Fehler: " + text);
      }
    } catch (err) {
      setStatus("❌ Netzwerkausnahme: " + err.message);
    }
  };

  return (
    <div style={{ padding: 24, fontFamily: "Arial" }}>
      <h2>Rechnungs-Extraktion Test UI</h2>

      <input
        type="file"
        accept="application/pdf"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />

      <button
        style={{ marginLeft: 12 }}
        onClick={analyze}
        disabled={!file}
      >
        Extrahieren & an YAMBS senden
      </button>

      <pre style={{ marginTop: 20, whiteSpace: "pre-wrap" }}>{status}</pre>
    </div>
  );
}