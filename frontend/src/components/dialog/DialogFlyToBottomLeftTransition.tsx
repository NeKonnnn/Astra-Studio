import React from 'react';
import { Transition } from 'react-transition-group';
import type { TransitionProps } from '@mui/material/transitions';
import useForkRef from '@mui/utils/useForkRef';
import getReactElementRef from '@mui/utils/getReactElementRef';

/** Длительность «улёта» диалога в левый нижний угол (мс). */
export const DIALOG_FLY_TO_CORNER_MS = 360;

const CORNER_OFFSET_PX = 36;

const CORNER_TRANSFORM = `translate(calc(-50vw + ${CORNER_OFFSET_PX}px), calc(50vh - ${CORNER_OFFSET_PX}px)) scale(0.04)`;
const CENTER_TRANSFORM = 'translate(0, 0) scale(1)';

const EASE_OUT = 'cubic-bezier(0.22, 1, 0.36, 1)';
const EASE_IN = 'cubic-bezier(0.55, 0.055, 0.675, 0.19)';

const styles: Record<string, React.CSSProperties> = {
  entering: {
    transform: CENTER_TRANSFORM,
    opacity: 1,
  },
  entered: {
    transform: CENTER_TRANSFORM,
    opacity: 1,
  },
  exiting: {
    transform: CORNER_TRANSFORM,
    opacity: 0,
  },
  exited: {
    transform: CORNER_TRANSFORM,
    opacity: 0,
  },
};

const hiddenStyles: React.CSSProperties = {
  transform: CORNER_TRANSFORM,
  opacity: 0,
  visibility: 'hidden',
};

function reflow(node: HTMLElement) {
  // eslint-disable-next-line @typescript-eslint/no-unused-expressions
  node.scrollTop;
}

function durationFor(
  timeout: TransitionProps['timeout'],
  mode: 'enter' | 'exit',
  style?: React.CSSProperties,
): number {
  if (style?.transitionDuration != null) {
    const raw = String(style.transitionDuration);
    if (raw.endsWith('ms')) return Number.parseFloat(raw);
    if (raw.endsWith('s')) return Number.parseFloat(raw) * 1000;
  }
  if (typeof timeout === 'number') return timeout;
  if (timeout && typeof timeout === 'object') {
    return timeout[mode] ?? DIALOG_FLY_TO_CORNER_MS;
  }
  return DIALOG_FLY_TO_CORNER_MS;
}

/**
 * Анимация MUI Dialog: появление из левого нижнего угла, исчезновение — «улёт» туда же.
 * React 19: обязательно nodeRef (без findDOMNode).
 */
const DialogFlyToBottomLeftTransition = React.forwardRef(function DialogFlyToBottomLeftTransition(
  props: TransitionProps & { children: React.ReactElement },
  ref: React.Ref<unknown>,
) {
  const {
    appear = true,
    children,
    easing,
    in: inProp,
    onEnter,
    onEntered,
    onEntering,
    onExit,
    onExited,
    onExiting,
    style,
    timeout = DIALOG_FLY_TO_CORNER_MS,
    addEndListener,
    ...other
  } = props;

  const nodeRef = React.useRef<HTMLElement | null>(null);
  const handleRef = useForkRef(nodeRef, getReactElementRef(children), ref);

  /** MUI enter-колбэки требуют boolean; react-transition-group передаёт optional. */
  const withEnterNode = (
    callback?: (node: HTMLElement, isAppearing: boolean) => void,
  ) => {
    return (maybeIsAppearing?: boolean) => {
      if (!callback) return;
      const node = nodeRef.current;
      if (!node) return;
      callback(node, Boolean(maybeIsAppearing));
    };
  };

  const withExitNode = (callback?: (node: HTMLElement) => void) => {
    return () => {
      if (!callback) return;
      const node = nodeRef.current;
      if (!node) return;
      callback(node);
    };
  };

  const handleEnter = withEnterNode((node, isAppearing) => {
    reflow(node);
    const ms = durationFor(timeout, 'enter', style);
    const ease =
      (typeof easing === 'object' ? easing.enter : easing) || EASE_OUT;
    node.style.transition = [
      `transform ${ms}ms ${ease}`,
      `opacity ${Math.round(ms * 0.75)}ms ease`,
    ].join(', ');
    onEnter?.(node, isAppearing);
  });

  const handleExit = withExitNode((node) => {
    const ms = durationFor(timeout, 'exit', style);
    const ease = (typeof easing === 'object' ? easing.exit : easing) || EASE_IN;
    node.style.transition = [
      `transform ${ms}ms ${ease}`,
      `opacity ${Math.round(ms * 0.75)}ms ease`,
    ].join(', ');
    onExit?.(node);
  });

  const handleExited = withExitNode((node) => {
    node.style.transition = '';
    onExited?.(node);
  });

  const handleAddEndListener = (next: () => void) => {
    if (addEndListener && nodeRef.current) {
      addEndListener(nodeRef.current, next);
    }
  };

  return (
    <Transition
      appear={appear}
      in={inProp}
      nodeRef={nodeRef}
      onEnter={handleEnter}
      onEntered={withEnterNode(onEntered)}
      onEntering={withEnterNode(onEntering)}
      onExit={handleExit}
      onExited={handleExited}
      onExiting={withExitNode(onExiting)}
      addEndListener={addEndListener ? handleAddEndListener : undefined}
      timeout={timeout}
      {...other}
    >
      {(state, childProps) => {
        // React 19: cloneElement принимает ref; узкий тип только со style ломает tsc.
        const child = children as React.ReactElement<{
          style?: React.CSSProperties;
          ref?: React.Ref<unknown>;
        }>;
        const { ownerState: _ownerState, ...restChildProps } = childProps as {
          ownerState?: unknown;
        } & Record<string, unknown>;
        const base =
          state === 'exited' && !inProp
            ? hiddenStyles
            : styles[state] || styles.exited;
        return React.cloneElement(child, {
          style: {
            transformOrigin: 'center center',
            willChange: 'transform, opacity',
            ...base,
            ...style,
            ...child.props.style,
          },
          ref: handleRef,
          ...restChildProps,
        });
      }}
    </Transition>
  );
});

export default DialogFlyToBottomLeftTransition;
