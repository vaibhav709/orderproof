import React, {useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import "./style.css";

const statuses = {needs_clarification:"Needs clarification", awaiting_review:"Awaiting review", approved:"Approved"};

async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "Request failed. Please try again.");
  }
  return response;
}

async function upload(file) {
  const body = new FormData();
  body.set("file", file, file.name || "sample.pdf");
  return (await request("/orders", {method:"POST", body})).json();
}

function Source({order, source}) {
  const original = order.original_evidence || order;
  return <section className="viewer" aria-label="Source document">
    <div className="viewer-head"><strong>Source document</strong><small>Page 1 of 1</small></div>
    <article className="paper">
      <strong className="letterhead">ALDER CHANNEL WORKS</strong><h2>Purchase order</h2>
      <p>{order.order_id}</p><small>Supplier</small><p>Fenmere Pump Supply</p>
      <table><thead><tr><th>Item description</th><th>Qty</th></tr></thead>
        <tbody><tr><td>Mechanical seal kit for pump {order.model}</td><td>{order.quantity} {order.unit}</td></tr></tbody></table>
      {original.serial && <p className="detail">Pump serial number: {original.serial}</p>}
      {original.revision && <p className="detail">Pump revision: {original.revision}</p>}
      <small>Fictional purchase order</small>
    </article>
    {source && <a className="source-link" href={source} target="_blank" rel="noreferrer">Open original PDF ↗</a>}
  </section>;
}

function SupplierRecords({order}) {
  const [open, setOpen] = useState(false);
  const [records, setRecords] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const url = "/orders/" + encodeURIComponent(order.order_id) + "/evidence?version=" + order.version;
    setError("");
    setRecords(null);
    request(url, {signal:controller.signal})
      .then(response => response.json())
      .then(data => {if (!controller.signal.aborted) setRecords(data);})
      .catch(e => {if (!controller.signal.aborted) setError(e.message);});
    return () => controller.abort();
  }, [open, order.order_id, order.version]);
  return <details className="supplier-records" onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>Related supplier records</summary>
    {error ? <p role="alert">{error}</p> : records === null ? <p>Loading records…</p> :
      records.length === 0 ? <p>No related records found.</p> :
      records.map(record => <div key={record.key}><strong>{record.key}</strong><pre>{record.text}</pre></div>)}
  </details>;
}

function ProductCatalog() {
  const [catalog, setCatalog] = useState(null);
  const [error, setError] = useState("");
  async function open(event) {
    if (!event.currentTarget.open || catalog) return;
    try {
      setError("");
      setCatalog(await (await request("/catalog")).json());
    } catch (e) {
      setError(e.message);
    }
  }
  return <details className="supplier-records" onToggle={open}>
    <summary>Browse supplier catalog</summary>
    {error && <p role="alert">{error}</p>}
    {catalog && <div style={{overflowX:"auto"}}><table>
      <caption>{catalog.products.every(p=>p.data_origin==="fictional") ? "Fictional supplier inventory" : "Supplier inventory"}</caption>
      <thead><tr>{["Part", "Description and material", "Fits", "Stock", "Pack size", "Lead time", "Bin"].map(h=><th key={h}>{h}</th>)}</tr></thead>
      <tbody>{catalog.products.map(p=><tr key={p.part_code}>
        <th>{p.part_code}</th><td>{p.description} · {p.material}</td>
        <td>{catalog.compatibility.filter(c=>c.part_code===p.part_code).map(c=>c.pump_model+" rev "+c.pump_revision).join(", ")}</td>
        <td>{p.stock_units} {p.sales_unit}</td><td>{p.kits_per_sales_unit}</td>
        <td>{p.lead_days} days</td><td>{p.bin_location}</td>
      </tr>)}</tbody>
    </table></div>}
  </details>;
}

function App() {
  const [order,setOrder] = useState(null), [source,setSource] = useState(""), [busy,setBusy] = useState(true);
  const [error,setError] = useState(""), [identifier,setIdentifier] = useState("");
  const input = useRef(null);
  const [demo,setDemo] = useState(null);
  const orderUrl = order && "/orders/" + encodeURIComponent(order.order_id);
  async function openSample(id) {
    const sample = await request("/samples/"+id);
    setDemo(sample.headers.get("X-OrderProof-Mode") === "fictional-demo");
    const pdf = await sample.blob();
    setOrder(await upload(pdf)); setSource("/samples/"+id); setIdentifier("");
  }
  async function run(action) {
    setBusy(true); setError("");
    try {await action();} catch (e) {setError(e.message);} finally {setBusy(false);}
  }
  useEffect(() => {run(() => openSample("PO-1002"));}, []);
  useEffect(() => () => {if(source.startsWith("blob:")) URL.revokeObjectURL(source);}, [source]);
  async function submit(e) {
    e.preventDefault();
    await run(async () => {
      const pending = order.status === "needs_clarification";
      const value = identifier.trim();
      const payload = {version:order.version};
      if (pending) payload[/^[ab]$/i.test(value) ? "revision" : "serial"] = value;
      const response = await request(orderUrl + (pending ? "/clarification" : "/approval"), {
        method:pending ? "PATCH":"POST", headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)
      });
      setOrder(await response.json()); setIdentifier("");
    });
  }
  async function download() {
    const response = await request(orderUrl + "/export");
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement("a"); a.href=url; a.download=order.order_id+"-approved.csv";
    a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
  }
  const pending = order?.status === "needs_clarification", approved = order?.status === "approved";
  return <><header><div className="bar"><b>OrderProof</b><span>Order review</span></div></header>
    <main><nav aria-label="Order selection">
      <label>Order review <select aria-label="Open a sample order" disabled={busy} value="" onChange={e=>run(()=>openSample(e.target.value))}>
        <option value="">Open sample…</option><option>PO-1001</option><option>PO-1002</option></select></label>
      {demo === false && <button className="secondary" disabled={busy} onClick={()=>input.current.click()}>Upload PDF</button>}
      <input ref={input} type="file" accept="application/pdf" hidden onChange={e=>{
        const file=e.target.files[0]; e.target.value=""; if(file) run(async()=>{
          setOrder(await upload(file)); setSource(URL.createObjectURL(file)); setIdentifier("");
        });
      }}/></nav>
      <ProductCatalog/>
      {demo && <p>Fictional sample orders only. You review the supplier evidence and approve each export.</p>}
      {error && <div role="alert">{error} {order && <button className="secondary" disabled={busy} onClick={()=>run(async()=>setOrder(await (await request(orderUrl)).json()))}>Reload draft</button>}</div>}
      {!order ? <p role="status">{busy ? "Opening order…" : "Choose a sample order or upload a PDF."}</p> :
      <><div className="title"><h1>{order.order_id}</h1><strong role="status" className={"badge "+order.status}>{statuses[order.status]}</strong></div>
      <div className="workspace"><Source order={order} source={source}/>
      <section className="review"><small>Line {order.line_number}</small><h2>{order.model} seal kit</h2>
      <p>Quantity　{order.quantity} {order.quantity === 1 ? "kit" : "kits"}</p><hr/>
      <h3>{pending ? "Pump identification" : "Compatibility evidence"}</h3>
      <p className="evidence">{order.match.reason}</p>
      <SupplierRecords key={order.order_id + "/" + order.version} order={order}/>
      <form onSubmit={submit}>{pending ? <label>Serial number or revision<input required maxLength={64} value={identifier} onChange={e=>setIdentifier(e.target.value)} placeholder="Enter identifier"/></label> :
        <label>Matched supplier part<output>{order.match.part_code}</output></label>}
      {approved ? <button type="button" disabled={busy} onClick={()=>run(download)}>Export CSV</button> :
        <button disabled={busy || (pending && !identifier.trim())}>{pending ? "Save clarification" : "Approve order"}</button>}
      </form></section></div></>}</main></>;
}
createRoot(document.getElementById("root")).render(<App/>);
