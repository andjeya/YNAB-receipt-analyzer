import { ReceiptDetailView } from "@/components/receipt-detail";

export default async function ReceiptDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ReceiptDetailView receiptId={id} />;
}
