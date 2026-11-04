import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CollectApp } from "@/components/collect/CollectApp";

export const metadata: Metadata = { title: "Quack: handwriting samples" };

export default function CollectPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <CollectApp />;
}
