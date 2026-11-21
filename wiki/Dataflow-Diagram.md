The original HTML diagram is preserved below. Copy it into a file named `dataflow.html` and open it in a browser to view it.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Swing-Trade Analyst — Data Flow Diagram (DFD)</title>
<style>
  :root {
    --text: #2C2C2B;
    --muted: #7D7A75;
    --canvas: #FFFFFF;
    --soft: #F9F8F7;
    --surface2: #F0EFED;
    --border: #E6E5E3;
    --blue: #2783DE;
    --blue-soft: #E5F2FC;
    --green: #46A171;
    --green-soft: #E8F1EC;
    --orange: #D5803B;
    --orange-soft: #FBEBDE;
    --red: #E56458;
    --red-soft: #FCE9E7;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--canvas);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.4;
    padding: 32px 40px;
    margin: 0;
  }
  .wrap { max-width: 1080px; margin: 0 auto; }
  header { margin-bottom: 24px; text-align: center; }
  h1 { font-size: 24px; font-weight: 700; margin: 0 0 6px; letter-spacing: -0.01em; }
  p.sub { color: var(--muted); font-size: 14px; margin: 0; }
  
  /* Diagram Grid / Elements */
  .dfd-container {
    display: flex;
    flex-direction: column;
    gap: 16px;
    margin-top: 24px;
  }
  
  .row {
    display: grid;
    grid-template-columns: 1fr;
    gap: 16px;
  }
  
  .flow-grid {
    display: grid;
    grid-template-columns: 1fr 24px 1.5fr 24px 1.5fr 24px 1fr;
    align-items: center;
  }
  
  /* Node Types */
  .node {
    border-radius: 8px;
    padding: 12px 14px;
    border: 1px solid var(--border);
    background: var(--canvas);
    position: relative;
    box-shadow: 0 1px 2px rgba(0,0,0,.03);
  }
  
  /* External Entity: Square / Double Border */
  .entity {
    border: 2px solid var(--text);
    background: var(--soft);
    font-weight: 650;
    text-align: center;
  }
  
  /* Process: Circle/Oval or Rounded Rect with Process ID */
  .process {
    border-radius: 24px;
    border: 1.5px solid var(--blue);
    background: var(--blue-soft);
  }
  .process .id {
    font-size: 11px;
    font-weight: 700;
    color: var(--blue);
    text-transform: uppercase;
    margin-bottom: 3px;
  }
  .process h3 {
    margin: 0 0 4px;
    font-size: 14px;
    font-weight: 650;
  }
  
  /* Data Store: Open on sides */
  .datastore {
    border-left: 3px solid var(--orange);
    border-right: 3px solid var(--orange);
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    background: var(--orange-soft);
    border-radius: 0;
  }
  .datastore .id {
    font-size: 11px;
    font-weight: 700;
    color: var(--orange);
    margin-bottom: 2px;
  }
  .datastore h3 {
    margin: 0;
    font-size: 13.5px;
    font-weight: 650;
  }
  
  .label-list {
    margin: 4px 0 0;
    padding-left: 14px;
    font-size: 11.5px;
    color: var(--muted);
  }
  .label-list li { margin: 2px 0; }
  
  /* Arrows & Labels */
  .arrow {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    color: var(--muted);
    font-size: 11px;
    text-align: center;
    font-weight: 500;
  }
  .arrow svg { display: block; margin: 4px 0; color: #C9C7C4; }
  .arrow.vert { height: 32px; }
  
  .arrow-label {
    background: var(--canvas);
    padding: 2px 6px;
    border-radius: 4px;
    border: 1px dashed var(--border);
    font-size: 10.5px;
    max-width: 140px;
    line-height: 1.2;
    z-index: 2;
  }
  
  /* Side-by-side splits */
  .split-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 32px;
  }
  
  .split-arrow-down {
    display: grid;
    grid-template-columns: 1fr 1fr;
    justify-items: center;
  }

  .legend {
    margin-top: 32px;
    border-top: 1px solid var(--border);
    padding-top: 16px;
    display: flex;
    justify-content: center;
    gap: 24px;
    font-size: 12px;
  }
  .legend-item { display: flex; align-items: center; gap: 8px; }
  .legend-box { width: 14px; height: 14px; border-radius: 3px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>AI Swing-Trade Analyst — Data Flow Diagram (DFD)</h1>
    <p class="sub">Level 1 Process and Data Store Flows — Trading Decision Support System</p>
  </header>

  <div class="dfd-container">
    
    <!-- LEVEL 0 / 1 START: External Input -->
    <div class="flow-grid" style="grid-template-columns: 1.2fr 48px 1.5fr 48px 1.5fr;">
      <!-- External Entity -->
      <div class="node entity">
        User / Investor
        <div style="font-size:11px; font-weight:normal; margin-top:4px; color:var(--muted)">External Entity</div>
      </div>
      
      <!-- Flow Arrow -->
      <div class="arrow">
        <span class="arrow-label">Ticker / Ref link</span>
        <svg width="24" height="14" viewBox="0 0 24 14"><path d="M0 7 H20 M14 1 L21 7 L14 13" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
      </div>

      <!-- Process 1.0 -->
      <div class="node process">
        <div class="id">Process 1.0</div>
        <h3>Input Normalization</h3>
        <p style="margin:2px 0 0; font-size:11.5px; color:var(--muted)">Validates format &amp; resolves trading platform links to US ticker</p>
      </div>

      <!-- Flow Arrow -->
      <div class="arrow">
        <span class="arrow-label">Normalized Ticker</span>
        <svg width="24" height="14" viewBox="0 0 24 14"><path d="M0 7 H20 M14 1 L21 7 L14 13" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
      </div>

      <!-- Data Store: Queue / Target -->
      <div class="node datastore">
        <div class="id">D1</div>
        <h3>Active Analysis Target</h3>
        <p style="margin:2px 0 0; font-size:11.5px; color:var(--muted)">Current Context (In-Memory / State)</p>
      </div>
    </div>

    <!-- Vert arrows pointing down to parallel tracks -->
    <div class="split-arrow-down" style="grid-template-columns: 1.15fr 1fr; margin: 4px 0;">
      <div class="arrow vert">
        <span class="arrow-label" style="transform:translateY(-2px)">Ticker context</span>
        <svg width="14" height="32" viewBox="0 0 14 32"><path d="M7 0 V26 M1 19 L7 27 L13 19" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
      </div>
      <div class="arrow vert">
        <span class="arrow-label" style="transform:translateY(-2px)">Ticker context</span>
        <svg width="14" height="32" viewBox="0 0 14 32"><path d="M7 0 V26 M1 19 L7 27 L13 19" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
      </div>
    </div>

    <!-- Split Parallel Tracks: Structured (Left) vs Unstructured (Right) -->
    <div class="split-2">
      
      <!-- LEFT: STRUCTURED DATA STREAM -->
      <div style="display:flex; flex-direction:column; gap:16px;">
        <div style="font-weight:600; font-size:12px; text-transform:uppercase; color:var(--muted); letter-spacing:.05em; text-align:center;">Structured Track</div>
        
        <!-- Process 2.0 -->
        <div class="node process">
          <div class="id">Process 2.0</div>
          <h3>Structured Data Ingestion</h3>
          <p style="margin:0; font-size:11.5px; color:var(--muted)">Queries financial APIs (YFinance, AlphaVantage, VIX, etc.)</p>
        </div>

        <!-- Arrow Down -->
        <div class="arrow vert">
          <span class="arrow-label">Raw Price &amp; Ratios</span>
          <svg width="14" height="24" viewBox="0 0 14 24"><path d="M7 0 V18 M1 13 L7 19 L13 13" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
        </div>

        <!-- Data Store 2 -->
        <div class="node datastore">
          <div class="id">D2</div>
          <h3>PostgreSQL Feature Store</h3>
          <ul class="label-list">
            <li>OHLCV Price History</li>
            <li>Computed Metrics (RSI, SMA 50/200, Volume)</li>
            <li>Fundamental Ratios (Debt/Equity, Growth, FCF)</li>
          </ul>
        </div>

        <!-- Arrow Down -->
        <div class="arrow vert">
          <span class="arrow-label">Features &amp; Techs</span>
          <svg width="14" height="24" viewBox="0 0 14 24"><path d="M7 0 V18 M1 13 L7 19 L13 13" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
        </div>

        <!-- Process 3.0 -->
        <div class="node process">
          <div class="id">Process 3.0</div>
          <h3>Deterministic Rules Engine</h3>
          <p style="margin:0; font-size:11.5px; color:var(--muted)">Applies risk budget, checklist filters, hard-vetoes, entry/exit math</p>
        </div>
      </div>

      <!-- RIGHT: UNSTRUCTURED DATA STREAM -->
      <div style="display:flex; flex-direction:column; gap:16px;">
        <div style="font-weight:600; font-size:12px; text-transform:uppercase; color:var(--muted); letter-spacing:.05em; text-align:center;">Unstructured RAG Track</div>
        
        <!-- Process 4.0 -->
        <div class="node process">
          <div class="id">Process 4.0</div>
          <h3>Document Ingestion &amp; Chunking</h3>
          <p style="margin:0; font-size:11.5px; color:var(--muted)">Ingests 10-K risk sections, earnings transcripts, news</p>
        </div>

        <!-- Arrow Down -->
        <div class="arrow vert">
          <span class="arrow-label">Document Chunks</span>
          <svg width="14" height="24" viewBox="0 0 14 24"><path d="M7 0 V18 M1 13 L7 19 L13 13" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
        </div>

        <!-- Data Store 3 -->
        <div class="node datastore">
          <div class="id">D3</div>
          <h3>Cloud Storage &amp; Vector DB</h3>
          <ul class="label-list">
            <li>Raw PDFs &amp; Summaries (GCS Bucket)</li>
            <li>Embeddings &amp; Vectors (pgvector / Vertex Search)</li>
          </ul>
        </div>

        <!-- Arrow Down -->
        <div class="arrow vert">
          <span class="arrow-label">Retrieved Passages</span>
          <svg width="14" height="24" viewBox="0 0 14 24"><path d="M7 0 V18 M1 13 L7 19 L13 13" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
        </div>

        <!-- Process 5.0 -->
        <div class="node process">
          <div class="id">Process 5.0</div>
          <h3>RAG Explanation Synthesis</h3>
          <p style="margin:0; font-size:11.5px; color:var(--muted)">Generates bullish/bearish summaries &amp; catalyst checks using LLM (Gemini)</p>
        </div>
      </div>

    </div>

    <!-- Convergence Arrows pointing to Process 6.0 (LangGraph decision layer) -->
    <div class="split-arrow-down" style="grid-template-columns: 1fr 1fr; margin-top: 12px;">
      <div class="arrow" style="justify-self: end; transform: translateX(-32px);">
        <span class="arrow-label">Risk parameters &amp; hard veto status</span>
        <svg width="14" height="32" viewBox="0 0 14 32"><path d="M7 0 V26 M1 19 L7 27 L13 19" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
      </div>
      <div class="arrow" style="justify-self: start; transform: translateX(32px);">
        <span class="arrow-label">Qualitative thesis &amp; red flags</span>
        <svg width="14" height="32" viewBox="0 0 14 32"><path d="M7 0 V26 M1 19 L7 27 L13 19" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
      </div>
    </div>

    <!-- Process 6.0 -->
    <div class="node process" style="margin: 0 auto; max-width: 700px; width: 100%;">
      <div class="id">Process 6.0</div>
      <h3 style="text-align: center;">LangGraph Orchestrated Fusion &amp; Critic Check</h3>
      <p style="margin:2px 0 0; font-size:12px; color:var(--muted); text-align: center;">
        Synthesizes numerical risk metrics with text summaries. Critic node scans for structural discrepancies or hallucinations before releasing recommendation.
      </p>
    </div>

    <!-- Arrow Down -->
    <div class="arrow vert" style="margin: 4px 0;">
      <span class="arrow-label">Final Decision &amp; Parameters (JSON)</span>
      <svg width="14" height="32" viewBox="0 0 14 32"><path d="M7 0 V26 M1 19 L7 27 L13 19" stroke="#C9C7C4" stroke-width="1.6" fill="none"/></svg>
    </div>

    <!-- Output Entity -->
    <div class="node entity" style="margin: 0 auto; max-width: 450px; width: 100%; border: 2px solid var(--green)">
      User Interface / Streamlit Dash
      <div style="font-size:11px; font-weight:normal; margin-top:4px; color:var(--muted)">
        Renders final decision (Trade / Watchlist / No trade) &amp; memo parameters
      </div>
    </div>

  </div>

  <!-- Legend -->
  <div class="legend">
    <div class="legend-item">
      <div class="legend-box" style="border: 2px solid var(--text); background: var(--soft)"></div>
      <span>External Entity</span>
    </div>
    <div class="legend-item">
      <div class="legend-box" style="border: 1.5px solid var(--blue); background: var(--blue-soft)"></div>
      <span>Process Node</span>
    </div>
    <div class="legend-item">
      <div class="legend-box" style="border-left: 3px solid var(--orange); border-right: 3px solid var(--orange); border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); background: var(--orange-soft)"></div>
      <span>Data Store</span>
    </div>
    <div class="legend-item">
      <span style="color: #C9C7C4; font-size:14px; font-weight:bold;">→</span>
      <span>Data Flow Direction</span>
    </div>
  </div>
</div>
</body>
</html>

```
