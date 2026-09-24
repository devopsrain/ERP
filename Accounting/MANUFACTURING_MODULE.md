# EBMS Manufacturing / Production Module

Functional description of the Manufacturing module, written for the Belayab Cable
Manufacturing PLC ERP tender. Every section below maps to a requirement in the
tender's production management list. The module is reached from the main menu
under **Manufacturing** (`/manufacturing`) and every screen is available in
English and Amharic, with Ethiopian calendar dates shown beside Gregorian dates.

All data is separated per company: a user only ever sees the plants, products,
orders and logs of the company selected in the portal.

---

## 1. Organisation and master data

| Tender requirement | How the module meets it |
|---|---|
| Structure manufacturing into plants by facility, location or product group | **Plants** page: code, name, location, product group, active flag. A default plant can be set in Settings and is attached to new orders. |
| Define work centres / production lines with capacity and cost | **Work Centres** page: process type (drawing, stranding, insulation, sheathing, armouring, packing, other), standard capacity per hour in kg / m / pcs, hourly cost rate used for conversion costing. |
| Register machines with their line, manufacturer, model and status | **Machines** page: each machine belongs to a work centre; status can be switched inline between *active*, *maintenance*, *down* and *retired*. Down / maintenance machines are counted on the dashboard. |
| Product master for finished and semi-finished cables | **Products** page: unique code per company, cross-section (mm²), colour, unit (m / kg / roll / drum), standard length per roll, SKU. A product can be mirrored into the Inventory module so finished goods receipts update stock. |
| Technical Data Sheets (TDS) with version control | On the product page: unlimited TDS versions holding conductor diameter, insulation thickness/diameter, laid-up diameter, bedding diameter, sheath thickness/diameter, resistance (Ω/km), rated voltage, applicable standard (e.g. IEC 60502) plus any extra parameters. Approving a new version automatically marks the earlier approved version *superseded*, so exactly one approved TDS exists per product. |
| Bill of Materials per product with history | BOM versions per product (draft → active → obsolete). Lines hold material code, name, quantity per output quantity, unit, scrap %, optional semi-finished component and the work centre / operation that consumes the material. Activating a version obsoletes the previous one but keeps it for traceability; active BOMs are frozen, changes create a new version. The dashboard lists every active product that still lacks an active BOM. |
| Process routings with machine parameters | Routing versions per product; each operation has a sequence, work centre, standard setup minutes, standard run minutes per unit and process parameters (die, nipple, zone temperature, diameter, lay length, thickness, free extras). |
| Factory calendar (holidays, shutdowns, maintenance, overhaul, renovation, upgrade) | **Factory Calendar** page per year and optionally per plant. These days are removed from available capacity. |
| Shift definitions with leaders and supervisors | **Shifts** page: name, start/end time (overnight shifts supported), optional work centre, shift leader, line supervisor. Shift hours drive the capacity model. |
| Downtime categories with unlimited predefined problems | **Downtime & Scrap Types** page: the four categories Mechanical, Electrical, Operational and Other are created automatically for each company; any number of reasons can be added under each, and further categories can be created. Scrap types (Copper, Aluminium, PVC, XLPE, Steel wire, Other, plus custom) are managed on the same page. |
| Standard material costs | **Material Costs** page: standard cost, unit and currency per material code, used for planned and actual costing. |
| GL integration settings | **Settings** page: WIP, raw-material inventory, finished-goods inventory and scrap accounts; default plant; automatic release request for make-to-order orders raised by the Commercial module. |

## 2. Planning

| Tender requirement | How the module meets it |
|---|---|
| Weekly / monthly / quarterly / annual production plans | **Production Plans**: header with period type, period dates and plant; lines per product with planned quantity, planned hours, target work centre and planning basis (demand, capacity or trend). Plans move draft → approved → closed. |
| Plan versus actual follow-up | The plan page shows, for each line, the actual output logged on the shop floor inside the plan period, the variance and variance %. |
| Long- and short-term capacity planning | **Capacity** page: for any period it computes *available hours* (working days from the factory calendar × daily shift hours applying to the work centre; 8 h/day when no shifts are defined) against *planned hours* from plan lines, showing utilisation %, a load bar and the theoretical output (available hours × standard capacity). Snapshots can be saved for later comparison. |
| Annual raw-material plan derived from the production plan and BOM | **Raw Material Plans**: "Explode from production plan" multiplies every plan line by the product's active BOM (recursively through semi-finished components, including scrap allowances), consolidates by material code, reads on-hand stock from Inventory when available and computes the quantity to procure with its estimated value at standard cost. Lines can also be added manually. |
| Submission to Property Administration, store and purchase requisitions | "Submit to Property Administration" creates one **store requisition** per material in the Inventory module and one **purchase requisition** (department Production) in the Procurement module, records their references on the plan and opens an approval request (`raw_material_plan`) in the Approval Engine. When a workflow is configured the plan waits for the decision; otherwise it is auto-approved. If a target module is not installed the user is told which step must be done manually. |

## 3. Production orders (make-to-stock and make-to-order)

| Tender requirement | How the module meets it |
|---|---|
| Manufacturing order numbering | Gapless numbers per company and year, e.g. `MO-2026-000123`. |
| Order content | Product, quantity and unit, cutting length, packing, customer and sales-order reference, delivery date, priority, plant, planned start/end, prepared / checked / approved by, notes. The approved TDS, active BOM and active routing are attached automatically and can be changed while the order is planned. |
| Make-to-order from sales orders | The Commercial module raises production orders through the module's programming interface; they carry the sales order number and customer, and can be released automatically when the setting is on. The Commercial module can also read back the status summary of all orders raised for a sales order. |
| Lifecycle Planning → Release → Confirmation → Closing with timestamps | Status planned → released → in progress → confirmed → closed (or cancelled). Every transition is time-stamped and written to the order history with the user who did it. |
| Release control with approvals | Release requires manager rights, checks the incoming-inspection gate (materials rejected by the Quality module block the release), then either releases directly or sends the release to the Approval Engine (`production_order`). An approved decision releases the order automatically; a rejection is shown on the order. |
| Operations and materials generated at release | Release copies the routing into order operations (with their process parameters) and explodes the BOM into the order's material list with planned quantities including scrap allowance. |
| Per-machine process parameters | Each order operation can be assigned to a specific machine of its work centre; its die, nipple, zone temperature, diameter, lay length, thickness and any other parameter are editable on the order, and its status is tracked pending → running → done. |
| Planned and actual costing with variance | Planned cost = BOM quantities × standard material cost + routing time × work-centre hourly rate. Actual cost = consumed material × cost + logged labour hours × work-centre rate. Variance (amount and %) is shown on each order, and per product on the dashboard and cycle report. |
| Order confirmation posts finished goods | Confirmation records a finished-goods transfer (quantity, rolls, under-length rolls, length, weight, reference, from Property Administration to Market Finished Goods Store), books an inventory receipt when the product is linked to an inventory item, and posts the finished-goods journal. |
| BOM print for a production order | "BOM print": printable sheet with order header, technical data, materials required for the order quantity (with issued quantities), operations with process parameters and signature lines. |

## 4. Shop-floor logging

| Tender requirement | How the module meets it |
|---|---|
| Hourly and per-shift output, input, intermediate status | **Shop-floor Logs** (and the log form on each order): date, hour slot, shift, machine, operation, operator, input quantity, output quantity, rolls, under-length rolls, length (m), weight (kg), scrap quantity and type, rework, lot and drum numbers, remarks. Yield (output / input) is computed on every line. The first log moves a released order to *in progress*. |
| Raw material issue, consumption and return | Material movements per order (issue / consumption / return) with lot, store reference and drum reference; the order's material list shows planned, issued, consumed and returned quantities. Consumption posts the WIP journal. |
| Downtime by category and reason | **Downtime** page and per-order form: machine, work centre, order, shift, start/end (minutes computed automatically or entered), category, predefined reason, description, reporter. |
| Scrap categorisation | Scrap quantity and scrap type on every log line; the scrap report pivots quantities by type. |
| Machine and labour time per batch, setup vs run | Labour logs per order / work centre / shift / date with number of workers, setup hours, run hours and total hours. |
| Finished-goods transfer Property Administration → Market store | Recorded at confirmation with reference number and both store names; visible on the order and in the "Finished goods delivered to store" report. |

## 5. Finance integration

* Consumption of raw material posts **Dr Work-in-progress / Cr Raw-material inventory** at standard cost.
* Confirmation posts **Dr Finished goods / Cr Work-in-progress** for the order's actual cost.
* Accounts come from the module settings; when they are blank, or the ledger is unavailable, production continues and the GL status is recorded as *skipped* or *failed* on the movement and order so Finance can follow up. Production is never blocked by a ledger problem.

## 6. Reports

All reports are HTML tables with a date range and plant / work centre / machine filters, a standard header (company, period, generated date) and footer, and an **Export Excel** button.

1. **Performance per machine** — machine, product, order, diameter, cross-section (mm²), input kg, output kg, output/input %, plan quantity, actual − plan, length, period.
2. **Raw material converted to finished goods** — order, size (mm²), description, colour, output rolls, under-length rolls, length (m), weight (kg), input (kg), with totals.
3. **Finished goods delivered to store** — date, order, size, description, colour, rolls, length, under-length quantity, kg, reference number, destination store.
4. **Raw material status per order** — one column group per material code (issued / consumed / returned), totals and drum reference numbers.
5. **Scrap generated** — per order and size, one column per scrap material (kg) and total.
6. **Machine utilisation and downtime** — daily or monthly: available hours, run hours, downtime hours and downtime by category, utilisation %, output and yield.
7. **Plan vs actual, efficiency, yield and wastage** — per line / work centre, machine, SKU/variant and shift: planned vs actual quantity, efficiency %, input, yield %, scrap, wastage %, rework.
8. **Production order cycle and cost variance** — planned, released, confirmed and closed dates, days to release, days in production, total days, quantities, planned vs actual cost and variance.
9. **Consumption and output journals** — every consumption and output posting with amount, GL status and journal reference.

Daily machine KPIs (input, output, scrap, available hours, run hours, downtime, yield %, utilisation %) are computed automatically every morning at 06:00 for the previous day and shown on the dashboard; a weekly job flags production orders that have passed their delivery date.

## 7. End-to-end process map

`/manufacturing/process-map` shows the tender's 16-step flow as a numbered vertical chart:

1. Customer purchase order received (Sales)
2. Sales order prepared and approved — 3 approvals (Sales / Management)
3. Manufacturing order, TDS and raw-material plan prepared (Planning & Engineering)
4. Store requisition and purchase requisition raised (Property Administration)
5. Purchase requisition received by Procurement office
6. Management approval of the purchase
7. Quotations, samples and purchase order (Procurement)
8. Incoming raw-material inspection (Quality)
9. Raw material received into store (Property Administration)
10. TDS and manufacturing order released to production (Planning & Engineering)
11. Production — drawing, stranding, insulation, sheathing, packing
12. In-process and final inspection (Quality)
13. Finished goods received into inventory (Property Administration)
14. Transfer to Market finished-goods store
15. Sales dispatch to customer
16. Customer acceptance

Entering a sales-order or manufacturing-order number colours each step *done*, *in progress* or *not started / module not installed* using live data from the Manufacturing, Commercial, Quality, Procurement, Inventory and Approval modules where they are installed, and every step links to the page that owns it.

## 8. Roles

* Any logged-in user can maintain master data, plans, orders and shop-floor logs.
* Manager rights are required to approve TDS, activate BOMs and routings, approve plans, release / confirm / close / cancel production orders, approve raw-material plans and change GL settings.
