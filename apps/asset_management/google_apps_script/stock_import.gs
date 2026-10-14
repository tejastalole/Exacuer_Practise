/**
 * Google Sheet <-> AM Stock Entry sync for the asset_management Frappe app.
 *
 * Deployed as a Web App. ERPNext (which may run on localhost) calls it on its sync schedule:
 *   GET  ?action=pending&token=...   -> rows whose Status needs to be applied
 *   POST {token, results: [...]}     -> writes ERPNext ID / Error back
 *
 * The Status column is set by the user and never overwritten:
 *   Pending   - not ready, nothing happens
 *   (empty)   - same as Draft for a new row; leaves an existing entry as it is
 *   Draft     - create the AM Stock Entry as a draft
 *   Submitted - create and submit it (or submit the existing draft)
 *   Cancelled - cancel the submitted entry
 * Editing a row whose entry is still a Draft updates that draft.
 * The Status cell's note stores the row's sync key, the state last applied in ERPNext
 * and a hash of the row data last applied.
 * Setup steps: see "Google Sheet stock import" in apps/asset_management/README.md.	
 */

const CONFIG = {
	SHEET_NAME: "Stock Import",
	MAX_ROWS_PER_SYNC: 50,
};

// ERPNext refuses to sync with an older script; must match SCRIPT_VERSION in google_sheet_sync.py.
const SCRIPT_VERSION = 2;

const HEADERS = {
	entry_type: "Entry Type",
	item_code: "Item Code",
	qty: "Qty",
	source_warehouse: "Source Warehouse",
	target_warehouse: "Target Warehouse",
	status: "Status",
	erpnext_id: "ERPNext ID",
	error: "Error",
	// Optional columns; used when present.
	company: "Company",
	posting_date: "Posting Date",
	rate: "Rate",
	remarks: "Remarks",
};

const INPUT_FIELDS = [
	"entry_type",
	"item_code",
	"qty",
	"source_warehouse",
	"target_warehouse",
	"company",
	"posting_date",
	"rate",
	"remarks",
];
const NEEDS_SOURCE = ["Material Issue", "Material Transfer"];
const NEEDS_TARGET = ["Material Receipt", "Material Transfer"];
const STATUSES = ["Pending", "Draft", "Submitted", "Cancelled"];

function doGet(e) {
	return handleRequest(e.parameter.token, () => getPendingRows());
}

function doPost(e) {
	const body = JSON.parse((e.postData && e.postData.contents) || "{}");
	return handleRequest(body.token, () => writeResults(body.results || []));
}

/** Simple trigger: editing a row clears its Error so the next sync tries it again. */
function onEdit(e) {
	const sheet = e.range.getSheet();
	if (sheet.getName() !== CONFIG.SHEET_NAME) return;

	const columns = getColumns(sheet);
	const watched = INPUT_FIELDS.concat(["status", "erpnext_id"]);
	const editedWatchedColumn = watched.some((field) => {
		const column = columns[field];
		return column && column >= e.range.getColumn() && column <= e.range.getLastColumn();
	});
	if (!editedWatchedColumn) return;

	const firstRow = Math.max(e.range.getRow(), 2);
	if (e.range.getLastRow() >= firstRow) {
		sheet.getRange(firstRow, columns.error, e.range.getLastRow() - firstRow + 1, 1).clearContent();
	}
}

function handleRequest(token, handler) {
	const expected = PropertiesService.getScriptProperties().getProperty("SYNC_TOKEN");
	if (!expected || token !== expected) {
		return jsonResponse({ error: "Invalid sync token" });
	}

	const lock = LockService.getScriptLock();
	lock.waitLock(30 * 1000);
	try {
		return jsonResponse(handler());
	} catch (error) {
		return jsonResponse({ error: error.message });
	} finally {
		lock.releaseLock();
	}
}

/**
 * A row is sent when its Status differs from the state last applied in ERPNext, or its
 * data changed, and its Error cell is empty. Failed rows wait until edited.
 */
function getPendingRows() {
	const spreadsheet = SpreadsheetApp.getActive();
	const sheet = getSheet();
	const columns = getColumns(sheet);
	const rows = [];

	const rowCount = sheet.getLastRow() - 1;
	if (rowCount > 0) {
		const timezone = spreadsheet.getSpreadsheetTimeZone();
		const values = sheet.getRange(2, 1, rowCount, sheet.getLastColumn()).getValues();
		const notes = sheet.getRange(2, columns.status, rowCount, 1).getNotes();
		const seenIds = {};
		const seenKeys = {};

		for (let i = 0; i < values.length && rows.length < CONFIG.MAX_ROWS_PER_SYNC; i++) {
			const rowNumber = i + 2;
			const erpnextId = String(cellValue(values[i], columns.erpnext_id)).trim();
			let meta = parseMeta(notes[i][0]);

			if (erpnextId) {
				if (seenIds[erpnextId]) {
					sheet
						.getRange(rowNumber, columns.error)
						.setValue(`Duplicate ERPNext ID ${erpnextId}; clear it to create a new entry`);
					continue;
				}
				seenIds[erpnextId] = true;
			}

			// A copied row brings the original's note; give it its own sync key.
			if (meta.sync_key && seenKeys[meta.sync_key] && !erpnextId) {
				meta = {};
				sheet.getRange(rowNumber, columns.status).clearNote();
			}
			if (meta.sync_key) seenKeys[meta.sync_key] = true;

			if (cellValue(values[i], columns.error)) continue;

			const row = readRow(values[i], columns, timezone);
			if (!erpnextId && INPUT_FIELDS.every((field) => row[field] === "")) continue;

			const statusText = String(cellValue(values[i], columns.status)).trim();
			const status = parseStatus(statusText);
			if (status === null) {
				sheet
					.getRange(rowNumber, columns.error)
					.setValue(`Unknown Status "${statusText}"; use Pending, Draft, Submitted, Cancelled or leave it empty`);
				continue;
			}
			if (status === "Pending") continue;
			const target = status || meta.state || "Draft";

			if (!erpnextId && target !== "Cancelled" && !isComplete(row)) continue;

			// ERPNext compares the row with the entry, so a row is resent whenever it differs
			// from what was last synced (including rows without a hash yet).
			const rowHash = hashRow(row);
			const dataChanged = meta.hash !== rowHash;
			if (target === meta.state && (target === "Cancelled" || !dataChanged)) continue;

			if (!meta.sync_key) {
				meta = { sync_key: Utilities.getUuid(), state: meta.state || "" };
				sheet.getRange(rowNumber, columns.status).setNote(JSON.stringify(meta));
			}

			rows.push(
				Object.assign(
					{
						sync_key: meta.sync_key,
						erpnext_id: erpnextId,
						target_status: target,
						row_hash: rowHash,
					},
					row
				)
			);
		}
	}

	return { version: SCRIPT_VERSION, spreadsheet: spreadsheet.getName(), sheet: CONFIG.SHEET_NAME, rows: rows };
}

function writeResults(results) {
	const sheet = getSheet();
	const columns = getColumns(sheet);
	const rowCount = sheet.getLastRow() - 1;
	if (rowCount < 1) return { updated: 0 };

	// Find rows by sync key so results land correctly even if rows were inserted or moved.
	const rowByKey = {};
	sheet
		.getRange(2, columns.status, rowCount, 1)
		.getNotes()
		.forEach((note, i) => {
			const meta = parseMeta(note[0]);
			if (meta.sync_key) rowByKey[meta.sync_key] = { rowNumber: i + 2, meta: meta };
		});

	let updated = 0;
	results.forEach((result) => {
		const found = rowByKey[result.sync_key];
		if (!found) return;

		const rowNumber = found.rowNumber;
		const meta = {
			sync_key: result.sync_key,
			state: result.state || "",
			hash: result.row_hash || found.meta.hash,
		};
		sheet.getRange(rowNumber, columns.status).setNote(JSON.stringify(meta));
		if (result.name) {
			sheet.getRange(rowNumber, columns.erpnext_id).setValue(result.name);
		}
		sheet.getRange(rowNumber, columns.error).setValue(result.error || "");
		updated++;
	});

	return { updated: updated };
}

/** Returns the matching status, "" when empty, or null when the text isn't a known status. */
function parseStatus(text) {
	const value = String(text || "").trim().toLowerCase();
	if (!value) return "";
	return STATUSES.find((status) => status.toLowerCase() === value) || null;
}

/** Includes the script version, so a new version resends every row once. */
function hashRow(row) {
	const digest = Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, JSON.stringify(row));
	return `v${SCRIPT_VERSION}:${Utilities.base64Encode(digest)}`;
}

function parseMeta(note) {
	try {
		const meta = JSON.parse(note || "{}");
		return meta && typeof meta === "object" ? meta : {};
	} catch (error) {
		return {};
	}
}

function getSheet() {
	const sheet = SpreadsheetApp.getActive().getSheetByName(CONFIG.SHEET_NAME);
	if (!sheet) throw new Error(`Sheet "${CONFIG.SHEET_NAME}" not found`);
	return sheet;
}

function getColumns(sheet) {
	const headers = sheet
		.getRange(1, 1, 1, sheet.getLastColumn())
		.getValues()[0]
		.map((header) => String(header).trim());

	const columns = {};
	Object.keys(HEADERS).forEach((field) => {
		const index = headers.indexOf(HEADERS[field]);
		if (index >= 0) columns[field] = index + 1;
	});

	["entry_type", "item_code", "qty", "status", "erpnext_id", "error"].forEach((field) => {
		if (!columns[field]) throw new Error(`Missing column "${HEADERS[field]}" in sheet ${CONFIG.SHEET_NAME}`);
	});
	return columns;
}

function readRow(rowValues, columns, timezone) {
	const row = {};
	INPUT_FIELDS.forEach((field) => {
		const value = cellValue(rowValues, columns[field]);
		if (value instanceof Date) {
			row[field] = Utilities.formatDate(value, timezone, "yyyy-MM-dd");
		} else if (typeof value === "string") {
			row[field] = value.trim();
		} else {
			row[field] = value;
		}
	});
	return row;
}

function isComplete(row) {
	if (!row.entry_type || !row.item_code || row.qty === "" || row.qty === null) return false;
	if (NEEDS_SOURCE.includes(row.entry_type) && !row.source_warehouse) return false;
	if (NEEDS_TARGET.includes(row.entry_type) && !row.target_warehouse) return false;
	return true;
}

function cellValue(rowValues, column) {
	return column ? rowValues[column - 1] : "";
}

function jsonResponse(data) {
	return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
}
