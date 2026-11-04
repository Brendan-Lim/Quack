"use client";

import { X } from "lucide-react";
import { useCallback, useEffect, useState, useSyncExternalStore, type ReactNode, type RefObject } from "react";
import type { PenStroke } from "@/lib/types";
import { useQuack, type QuackOptions } from "@/hooks/useQuack";
import { PEN_COLORS, PEN_WIDTHS, Toolbar } from "./controls/Toolbar";
import { DebugPanel } from "./controls/DebugPanel";
import { buildFixture, downloadFixture } from "@/lib/qaFixture";
import { AskPanel } from "./ask/AskPanel";
import { DuckCompanion } from "./duck/DuckCompanion";
import { NotebookCanvas, type Paper, type Tool } from "./notebook/NotebookCanvas";

const PRIVACY_KEY = "quack.privacy-seen";

// What opens over the notebook can see the page, and bring a place on it back into view.
export type NotebookHandle = { strokes: () => readonly PenStroke[]; bringIntoView: (y: number) => void };

type Props = {
  options?: QuackOptions;
  // Finish session and the account menu, for someone signed in.
  accountActions?: ReactNode;
  notebookHandle?: RefObject<NotebookHandle | null>;
  // Another page is open over the notebook, so it's out of reach of the keyboard as well as the eye.
  covered?: boolean;
};

export function QuackApp({ options, accountActions, notebookHandle, covered = false }: Props = {}) {
  const quack = useQuack(options);
  const { store, duck, dismiss, cancelSelection, selection, selectionGone } = quack;
  const notebook = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  const [tool, setTool] = useState<Tool>("pen");
  const [color, setColor] = useState(PEN_COLORS[0].value);
  const [width, setWidth] = useState(PEN_WIDTHS[1].value);
  const [paper, setPaper] = useState<Paper>("grid");
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [showPrivacy, setShowPrivacy] = useState(false);
  const [debug, setDebug] = useState(false);
  const [focus, setFocus] = useState<{ y: number } | null>(null);

  useEffect(() => {
    if (!notebookHandle) return;
    notebookHandle.current = { strokes: () => store.getState().strokes, bringIntoView: (y) => setFocus({ y }) };
    return () => {
      notebookHandle.current = null;
    };
  }, [notebookHandle, store]);

  const chooseTool = useCallback(
    (next: Tool) => {
      // Going back to writing puts the question away. What was circled is untouched.
      cancelSelection();
      setTool(next);
    },
    [cancelSelection],
  );

  const closeSelection = useCallback(() => {
    cancelSelection();
    setTool("pen");
  }, [cancelSelection]);

  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect -- reads browser-only state after mount */
    setShowPrivacy(!localStorage.getItem(PRIVACY_KEY));
    setDebug(new URLSearchParams(window.location.search).has("debug"));
    /* eslint-enable react-hooks/set-state-in-effect */
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- it's the notes changing that ends it
    if (selectionGone) closeSelection();
  }, [selectionGone, closeSelection]);

  useEffect(() => {
    if (covered) return;
    const onKey = (event: KeyboardEvent) => {
      // A dialog takes the keyboard while it's open, so Escape closes only that.
      if (confirmingClear) {
        if (event.key === "Escape") setConfirmingClear(false);
        return;
      }
      if (event.key === "Escape") {
        if (selection) closeSelection();
        else dismiss();
        return;
      }
      if ((event.target as HTMLElement).tagName === "INPUT") return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) store.redo();
        else store.undo();
      } else if (event.key === "p") chooseTool("pen");
      else if (event.key === "e") chooseTool("eraser");
      else if (event.key === "l") chooseTool("lasso");
      else if (event.key === "`") setDebug((current) => !current);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [store, dismiss, confirmingClear, selection, closeSelection, chooseTool, covered]);

  const dismissPrivacy = useCallback(() => {
    localStorage.setItem(PRIVACY_KEY, "1");
    setShowPrivacy(false);
  }, []);

  return (
    <main inert={covered} className="flex h-dvh flex-col overflow-hidden bg-paper">
      <Toolbar
        tool={tool}
        color={color}
        width={width}
        paper={paper}
        canUndo={notebook.undoStack.length > 0}
        canRedo={notebook.redoStack.length > 0}
        monitoring={quack.monitoring}
        muted={quack.muted}
        taskContext={quack.taskContext}
        onTaskContext={quack.setTaskContext}
        onTool={chooseTool}
        onColor={(value) => {
          setColor(value);
          chooseTool("pen");
        }}
        onWidth={(value) => {
          setWidth(value);
          chooseTool("pen");
        }}
        onPaper={setPaper}
        onUndo={store.undo}
        onRedo={store.redo}
        onClear={() => setConfirmingClear(true)}
        onMonitoring={quack.toggleMonitoring}
        onMuted={quack.toggleMuted}
        onCheck={quack.checkNow}
        onImport={quack.importFile}
        accountActions={accountActions}
      />

      <NotebookCanvas
        store={store}
        tool={tool}
        color={color}
        width={width}
        paper={paper}
        markers={quack.markers}
        onDrawingChange={quack.setDrawing}
        lasso={selection?.polygon ?? null}
        selectedStrokes={selection?.strokeIds.length ?? 0}
        onLassoStart={cancelSelection}
        onLasso={(polygon, screen) => {
          if (!quack.finishLasso(polygon, screen)) setTool("pen");
        }}
        focus={focus}
      />

      {selection && (
        <AskPanel
          key={selection.strokeIds.join(",")}
          selection={selection}
          onAsk={(action, question) => {
            void quack.askAboutCircled(action, question).then((handedOver) => {
              if (handedOver) setTool("pen");
            });
          }}
          onClarify={quack.answerClarificationForCircled}
          onCancel={closeSelection}
        />
      )}

      <div className="pointer-events-none fixed bottom-4 left-3 z-20 flex max-w-[min(340px,calc(100vw-9rem))] flex-col items-start gap-2 sm:left-5">
        {quack.importedImage && (
          <figure className="pointer-events-auto relative rounded-xl bg-white p-1.5 shadow-md ring-1 ring-ink/5">
            {/* eslint-disable-next-line @next/next/no-img-element -- a local data URL */}
            <img src={quack.importedImage} alt="Imported page" className="max-h-40 rounded-lg" data-testid="import-preview" />
            <button type="button" data-testid="import-close" aria-label="Close imported page" onClick={quack.closeImport} className="absolute top-1 right-1 flex size-11 items-center justify-center rounded-full bg-white/90 text-muted shadow-sm">
              <X size={14} aria-hidden />
            </button>
          </figure>
        )}
        <div role="status" aria-live="polite" className="flex flex-col items-start gap-2">
          {(quack.saveIssue ?? duck.issue) && (
            <p data-testid="issue" className="pointer-events-auto rounded-2xl bg-white/95 px-3 py-1.5 text-[13px] text-alert shadow-sm ring-1 ring-ink/5">
              {(quack.saveIssue ?? duck.issue)!.message}
            </p>
          )}
        </div>
        {showPrivacy && (
          <div data-testid="privacy" className="pointer-events-auto relative rounded-xl bg-white p-3 pr-9 text-[13px] leading-snug text-muted shadow-md ring-1 ring-ink/5">
            When you pause, Quack sends a crop of your recent lines to Meta&apos;s AI API to check them. Your notes stay in this
            browser. Tap the eye to stop watching.
            <button type="button" aria-label="Got it" onClick={dismissPrivacy} className="absolute top-1 right-1 flex size-11 items-center justify-center rounded-full text-muted hover:bg-ink/5">
              <X size={14} aria-hidden />
            </button>
          </div>
        )}
      </div>

      <DuckCompanion
        duck={duck}
        appearance={quack.duckAppearance}
        canTalk={quack.canTalk}
        recording={quack.recording}
        transcript={quack.lastTranscript}
        onTap={quack.tapDuck}
        onSelect={quack.selectMistake}
        onHint={() => void quack.askForHelp()}
        onExplain={(question) => void quack.explain(question)}
        onDismiss={quack.dismiss}
        onTalkStart={() => void quack.startTalking()}
        onTalkStop={() => void quack.stopTalking()}
        onClarify={quack.answerClarification}
        onDismissClarification={quack.dismissClarification}
      />

      {debug && (
        <DebugPanel
          duckState={duck.state}
          version={notebook.version}
          strokes={notebook.strokes.length}
          trace={quack.trace}
          onExport={() => downloadFixture(buildFixture(notebook.strokes, quack.selection?.strokeIds ?? null, notebook.version, quack.selection?.reply ?? null))}
        />
      )}

      {confirmingClear && (
        <div
          className="fixed inset-0 z-30 flex items-center justify-center bg-ink/20 p-4"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="clear-title"
          onKeyDown={(event) => {
            // Keep Tab inside the dialog: there are two buttons, so it just moves between them.
            if (event.key !== "Tab") return;
            const buttons = event.currentTarget.querySelectorAll("button");
            const target = document.activeElement === buttons[0] ? buttons[1] : buttons[0];
            event.preventDefault();
            target.focus();
          }}
        >
          <div className="w-full max-w-xs rounded-2xl bg-white p-5 shadow-xl">
            <h2 id="clear-title" className="font-display text-lg font-bold">Clear this page?</h2>
            <p className="mt-1 text-sm text-muted">You can still undo this afterwards.</p>
            <div className="mt-4 flex justify-end gap-2 text-sm font-medium">
              <button type="button" autoFocus onClick={() => setConfirmingClear(false)} className="min-h-11 rounded-lg px-3 py-2 hover:bg-ink/5">Keep it</button>
              <button
                type="button"
                data-testid="clear-confirm"
                onClick={() => {
                  store.clear();
                  setConfirmingClear(false);
                }}
                className="min-h-11 rounded-lg bg-alert px-3 py-2 text-white hover:opacity-90"
              >
                Clear page
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
