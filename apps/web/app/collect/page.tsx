import type { Metadata } from "next";
import { CollectApp } from "@/components/collect/CollectApp";

export const metadata: Metadata = { title: "Quack: handwriting samples" };

export default function CollectPage() {
  return <CollectApp />;
}
