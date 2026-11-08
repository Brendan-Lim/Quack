import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";

// Keep production builds independent of Google Fonts availability.
const display = localFont({ src: "./fonts/bricolage-grotesque-latin-700.woff2", variable: "--font-bricolage", weight: "700", display: "swap" });
const sans = localFont({ src: "./fonts/figtree-latin-variable.woff2", variable: "--font-figtree", weight: "300 900", display: "swap" });

export const metadata: Metadata = {
  title: "Quack",
  description: "A small duck that notices mistakes in your handwritten working and lets you find them yourself.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#FFFCF4",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Chrome on iPad adds `__gcrremoteframetoken` to <html> before React loads. That was round 4's red "1 Issue"
  // badge, from next-development.log. It's the browser's attribute, so on this one element the page as the
  // browser has it is kept. Everything inside is still checked: this doesn't reach below <html>.
  return (
    <html lang="en" className={`${display.variable} ${sans.variable} h-full antialiased`} suppressHydrationWarning>
      <body className="h-full">{children}</body>
    </html>
  );
}
