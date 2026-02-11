import { ReceiptDetailView } from "@/components/receipt-detail";

export default function ReceiptDetailPage({ params }: { params: { id: string } }) {
  return <ReceiptDetailView receiptId={params.id} />;
}
