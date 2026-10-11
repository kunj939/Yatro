import type { Metadata } from "next";
import { AuthPage } from "@/components/AuthPage";

export const metadata: Metadata = { title: "Create account" };
export default function RegisterPage() { return <AuthPage mode="register" />; }
