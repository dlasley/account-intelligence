import type { Metadata } from "next";
import "./globals.css";
import PostHogProvider from "@/components/PostHogProvider";
import Header from "@/components/Header";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Geist, Geist_Mono } from "next/font/google";
import { cn } from "@/lib/utils";

const geistSans = Geist({ subsets: ["latin"], variable: "--font-sans" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "Account Intelligence",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={cn("font-sans", geistSans.variable, geistMono.variable)}>
      <body>
        <PostHogProvider>
          <TooltipProvider>
            <Header />
            {children}
          </TooltipProvider>
        </PostHogProvider>
      </body>
    </html>
  );
}
