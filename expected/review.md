# Manually checked review outcomes

These are fictional development examples. Only the supplier directory is retrieval material.

| Order and line | Evidence and decision | Expected export after review |
| --- | --- | --- |
| PO-1001 / 1 | The order asks for two PX-40 seal kits. Serial 18B042 has suffix 042, inside SB-040's inclusive 040–099 range. The PX-40 revision B row in the parts table specifies SK-40B. Its catalog sales unit contains one kit, so the reviewer can approve two units. | One row for two KIT of SK-40B, assuming human approval. |
| PO-1002 / 1 | The buyer supplies only the PX-40 model and quantity. The parts table lists SK-40A for revision A and SK-40B for revision B. Quantity supplies no compatibility evidence. Leave the part code unset and ask “What is the serial number or revision of your PX-40 pump?” | Zero rows while awaiting the buyer's reply. |

## Approved CSV contract

One data row per human-approved order line. A file with zero approved lines contains the header alone. The companion CSV is the expected combined export after approving PO-1001.

| Column in file order | Meaning |
| --- | --- |
| order_id | Buyer PO identifier, preserved as text. |
| line_number | Positive integer line number from the PO. Together with order_id it identifies one line. |
| part_code | Exact supplier catalog code selected for this line. |
| quantity | Positive whole number of catalog sales units approved. |
| sales_unit | Catalog unit for the selected part. KIT represents one kit in these fixtures. |
