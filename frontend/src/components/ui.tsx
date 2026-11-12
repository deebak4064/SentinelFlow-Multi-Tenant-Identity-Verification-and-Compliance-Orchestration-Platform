/* shadcn/ui-style primitives (Radix + cva), tuned to the SentinelFlow token set. */
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cva, type VariantProps } from "class-variance-authority";
import { X } from "lucide-react";
import { forwardRef, type ComponentProps, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-[var(--radius-control)] text-[13px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-emerald text-[#04140e] hover:bg-[#35b386]",
        secondary: "border border-line-strong bg-raised text-fg hover:border-faint",
        ghost: "text-muted hover:bg-raised hover:text-fg",
        danger: "border border-rose/40 bg-rose-soft text-[#f0b2ab] hover:border-rose",
      },
      size: { sm: "h-7 px-2.5", md: "h-8 px-3", lg: "h-10 px-4 text-sm", icon: "size-8" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export const Button = forwardRef<HTMLButtonElement, ComponentProps<"button"> & VariantProps<typeof buttonVariants>>(
  ({ className, variant, size, type = "button", ...props }, ref) => (
    <button ref={ref} type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  ),
);
Button.displayName = "Button";

const control = "w-full rounded-[var(--radius-control)] border border-line bg-base px-2.5 text-[13px] text-fg placeholder:text-faint hover:border-line-strong focus:border-cyan focus:outline-none aria-[invalid=true]:border-rose";

export const Input = forwardRef<HTMLInputElement, ComponentProps<"input">>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn(control, "h-8", className)} {...p} />
));
Input.displayName = "Input";

export const Textarea = forwardRef<HTMLTextAreaElement, ComponentProps<"textarea">>(({ className, ...p }, ref) => (
  <textarea ref={ref} className={cn(control, "min-h-20 py-2 leading-relaxed", className)} {...p} />
));
Textarea.displayName = "Textarea";

export const Select = forwardRef<HTMLSelectElement, ComponentProps<"select">>(({ className, ...p }, ref) => (
  <select ref={ref} className={cn(control, "h-8 cursor-pointer pr-7", className)} {...p} />
));
Select.displayName = "Select";

export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: string; error?: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-[12.5px] font-medium text-fg/90">{label}</label>
      {children}
      {error ? <p role="alert" className="text-xs text-rose">{error}</p> : hint ? <p className="text-xs text-faint">{hint}</p> : null}
    </div>
  );
}

const tones = {
  neutral: "border-line-strong text-muted",
  emerald: "border-emerald/35 bg-emerald-soft text-[#7fd4b2]",
  cyan: "border-cyan/35 bg-cyan-soft text-[#9fd0db]",
  amber: "border-amber/35 bg-amber-soft text-[#e8c27f]",
  rose: "border-rose/40 bg-rose-soft text-[#eba79f]",
};
export type Tone = keyof typeof tones;

export function Badge({ tone = "neutral", children, className, dot }: { tone?: Tone; children: ReactNode; className?: string; dot?: boolean }) {
  return (
    <span className={cn("inline-flex h-5 items-center gap-1.5 rounded-full border px-2 text-[11.5px] font-medium", tones[tone], className)}>
      {dot && <span className="size-1.5 rounded-full bg-current" aria-hidden />}
      {children}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4", className)} aria-hidden />;
}

/* ---------------------------------------------------------------- dialog */
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({ title, description, children, className }: { title: string; description?: string; children: ReactNode; className?: string }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px]" />
      <DialogPrimitive.Content
        className={cn("panel fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto p-5 shadow-[0_24px_60px_-20px_rgba(0,0,0,.7)]", className)}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <DialogPrimitive.Title className="text-[15px] font-semibold">{title}</DialogPrimitive.Title>
            {description ? <DialogPrimitive.Description className="mt-1 text-[13px] text-muted">{description}</DialogPrimitive.Description> : <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>}
          </div>
          <DialogPrimitive.Close className="rounded p-1 text-muted hover:bg-raised hover:text-fg" aria-label="Close">
            <X className="size-4" />
          </DialogPrimitive.Close>
        </div>
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

/* ---------------------------------------------------------------- tabs */
export const Tabs = TabsPrimitive.Root;
export const TabsContent = TabsPrimitive.Content;
export function TabsList({ tabs }: { tabs: { value: string; label: ReactNode }[] }) {
  return (
    <TabsPrimitive.List className="flex gap-5 border-b border-line">
      {tabs.map((t) => (
        <TabsPrimitive.Trigger
          key={t.value}
          value={t.value}
          className="-mb-px border-b-2 border-transparent pb-2.5 text-[13px] text-muted hover:text-fg data-[state=active]:border-emerald data-[state=active]:text-fg"
        >
          {t.label}
        </TabsPrimitive.Trigger>
      ))}
    </TabsPrimitive.List>
  );
}

/* ---------------------------------------------------------------- dropdown */
export const Dropdown = DropdownPrimitive.Root;
export const DropdownTrigger = DropdownPrimitive.Trigger;
export function DropdownContent({ children }: { children: ReactNode }) {
  return (
    <DropdownPrimitive.Portal>
      <DropdownPrimitive.Content align="end" sideOffset={6} className="panel z-50 min-w-48 p-1 shadow-xl">
        {children}
      </DropdownPrimitive.Content>
    </DropdownPrimitive.Portal>
  );
}
export function DropdownItem({ children, onSelect, className }: { children: ReactNode; onSelect?: () => void; className?: string }) {
  return (
    <DropdownPrimitive.Item onSelect={onSelect} className={cn("flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-[13px] outline-none data-[highlighted]:bg-raised", className)}>
      {children}
    </DropdownPrimitive.Item>
  );
}
export const DropdownSeparator = () => <DropdownPrimitive.Separator className="my-1 h-px bg-line" />;
export const DropdownLabel = ({ children }: { children: ReactNode }) => <DropdownPrimitive.Label className="px-2 py-1.5 text-xs text-faint">{children}</DropdownPrimitive.Label>;

/* ---------------------------------------------------------------- table */
export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  );
}
export const Th = ({ children, className, ...p }: ComponentProps<"th">) => (
  <th className={cn("h-9 border-b border-line px-3 text-left text-xs font-medium text-faint first:pl-4 last:pr-4", className)} {...p}>{children}</th>
);
export const Td = ({ children, className, ...p }: ComponentProps<"td">) => (
  <td className={cn("h-11 border-b border-line/60 px-3 align-middle first:pl-4 last:pr-4", className)} {...p}>{children}</td>
);
