"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Check, FileText, Loader2, UploadCloud } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

export function ResumeUpload({
  initialName,
  initialOk,
}: {
  initialName: string;
  initialOk: boolean;
}) {
  const [current, setCurrent] = useState(initialOk ? initialName : null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [justUploaded, setJustUploaded] = useState(false);

  async function upload(file: File) {
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      toast.error("Please choose a PDF file.");
      return;
    }
    setUploading(true);
    try {
      const r = await api.uploadResume(file);
      setCurrent(r.resume_name);
      setJustUploaded(true);
      setTimeout(() => setJustUploaded(false), 2000);
      toast.success(`Attached ${r.resume_name}`);
    } catch (e) {
      toast.error(String((e as Error).message ?? e));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-3">
      {current && (
        <div className="flex items-center gap-3 rounded-lg border border-border bg-secondary/50 px-3.5 py-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-md bg-brand-soft text-brand">
            <FileText className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{current}</p>
            <p className="text-xs text-muted-foreground">
              Attached to every email you send.
            </p>
          </div>
          <span
            className={cn(
              "flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
              justUploaded
                ? "border-ok/30 bg-ok-soft text-ok"
                : "border-border bg-background text-muted-foreground",
            )}
          >
            <Check className="size-3" />
            {justUploaded ? "Saved" : "Attached"}
          </span>
        </div>
      )}

      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) upload(file);
        }}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-7 text-center transition-colors",
          dragOver
            ? "border-brand bg-brand-soft/50"
            : "border-input hover:border-brand/60 hover:bg-secondary/40",
          uploading && "pointer-events-none opacity-70",
        )}
      >
        <input
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
            e.target.value = "";
          }}
        />
        {uploading ? (
          <>
            <Loader2 className="size-5 animate-spin text-brand" />
            <span className="text-sm text-muted-foreground">Uploading…</span>
          </>
        ) : (
          <>
            <span className="grid size-10 place-items-center rounded-full bg-secondary text-muted-foreground">
              <UploadCloud className="size-5" />
            </span>
            <span className="text-sm">
              <span className="font-medium text-foreground">
                Click to {current ? "replace" : "upload"}
              </span>{" "}
              or drag a PDF here
            </span>
            <span className="text-xs text-muted-foreground">
              {current
                ? "Replaces your current resume"
                : "PDF only · attached to every email"}
            </span>
          </>
        )}
      </label>

    </div>
  );
}
