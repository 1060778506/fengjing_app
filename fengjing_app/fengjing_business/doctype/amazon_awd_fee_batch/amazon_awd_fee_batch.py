from decimal import Decimal

from frappe.model.document import Document


class AmazonAWDFeeBatch(Document):
	def validate(self):
		processing = Decimal("0")
		transportation = Decimal("0")
		storage = Decimal("0")
		for row in self.get("details") or []:
			amount = Decimal(str(row.charged_amount or 0))
			if row.source_type == "Processing":
				processing += amount
			elif row.source_type == "Transportation":
				transportation += amount
			elif row.source_type == "Storage":
				storage += amount
		self.processing_total = float(processing.quantize(Decimal("0.01")))
		self.transportation_total = float(transportation.quantize(Decimal("0.01")))
		self.storage_total = float(storage.quantize(Decimal("0.01")))
		self.total_amount = float((processing + transportation + storage).quantize(Decimal("0.01")))
		self.row_count = len(self.get("details") or [])
