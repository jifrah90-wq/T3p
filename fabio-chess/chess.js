// Fabio Chess — complete two-player chess game (no API, pure JS)
(function () {
  'use strict';

  // ── Piece constants ──
  const W = 'w', B = 'b';
  const KING = 'k', QUEEN = 'q', ROOK = 'r', BISHOP = 'b', KNIGHT = 'n', PAWN = 'p';

  // Unicode pieces
  const GLYPHS = {
    wk: '\u2654', wq: '\u2655', wr: '\u2656', wb: '\u2657', wn: '\u2658', wp: '\u2659',
    bk: '\u265A', bq: '\u265B', br: '\u265C', bb: '\u265D', bn: '\u265E', bp: '\u265F'
  };

  // Piece values for captured-piece ordering
  const PIECE_VALUE = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 };

  // ── Initial board (8×8, row 0 = rank 8 / Black back rank) ──
  function startPosition() {
    return [
      [pc(B,ROOK),pc(B,KNIGHT),pc(B,BISHOP),pc(B,QUEEN),pc(B,KING),pc(B,BISHOP),pc(B,KNIGHT),pc(B,ROOK)],
      [pc(B,PAWN),pc(B,PAWN),pc(B,PAWN),pc(B,PAWN),pc(B,PAWN),pc(B,PAWN),pc(B,PAWN),pc(B,PAWN)],
      [null,null,null,null,null,null,null,null],
      [null,null,null,null,null,null,null,null],
      [null,null,null,null,null,null,null,null],
      [null,null,null,null,null,null,null,null],
      [pc(W,PAWN),pc(W,PAWN),pc(W,PAWN),pc(W,PAWN),pc(W,PAWN),pc(W,PAWN),pc(W,PAWN),pc(W,PAWN)],
      [pc(W,ROOK),pc(W,KNIGHT),pc(W,BISHOP),pc(W,QUEEN),pc(W,KING),pc(W,BISHOP),pc(W,KNIGHT),pc(W,ROOK)]
    ];
  }

  function pc(color, type) { return { color, type }; }
  function inBounds(r, c) { return r >= 0 && r < 8 && c >= 0 && c < 8; }
  function cloneBoard(b) { return b.map(row => row.map(sq => sq ? { ...sq } : null)); }

  // ── Game state ──
  let board, turn, selected, legalCache, history, lastMove;
  let castleRights; // { wk, wq, bk, bq }
  let enPassantTarget; // { row, col } or null
  let halfMoveClock;
  let positionCounts; // for threefold repetition
  let capturedWhite, capturedBlack; // pieces captured BY each side

  function initGame() {
    board = startPosition();
    turn = W;
    selected = null;
    legalCache = null;
    history = [];
    lastMove = null;
    castleRights = { wk: true, wq: true, bk: true, bq: true };
    enPassantTarget = null;
    halfMoveClock = 0;
    positionCounts = {};
    capturedWhite = [];
    capturedBlack = [];
    recordPosition();
    render();
  }

  // ── Position hash for repetition detection ──
  function boardHash() {
    let h = '';
    for (let r = 0; r < 8; r++)
      for (let c = 0; c < 8; c++) {
        const p = board[r][c];
        h += p ? (p.color + p.type) : '--';
      }
    h += turn;
    h += (castleRights.wk?'1':'0')+(castleRights.wq?'1':'0')+(castleRights.bk?'1':'0')+(castleRights.bq?'1':'0');
    if (enPassantTarget) h += enPassantTarget.row + '' + enPassantTarget.col;
    return h;
  }

  function recordPosition() {
    const h = boardHash();
    positionCounts[h] = (positionCounts[h] || 0) + 1;
  }

  // ── Move generation ──
  function pseudoMoves(b, color, ep, castle) {
    const moves = [];
    const dir = color === W ? -1 : 1;
    const startRow = color === W ? 6 : 1;

    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const p = b[r][c];
        if (!p || p.color !== color) continue;

        switch (p.type) {
          case PAWN: {
            // Forward
            const nr = r + dir;
            if (inBounds(nr, c) && !b[nr][c]) {
              moves.push({ fr: r, fc: c, tr: nr, tc: c });
              // Double push
              const nr2 = r + 2 * dir;
              if (r === startRow && !b[nr2][c])
                moves.push({ fr: r, fc: c, tr: nr2, tc: c });
            }
            // Captures
            for (const dc of [-1, 1]) {
              const nc = c + dc;
              if (!inBounds(nr, nc)) continue;
              if (b[nr][nc] && b[nr][nc].color !== color)
                moves.push({ fr: r, fc: c, tr: nr, tc: nc });
              // En passant
              if (ep && ep.row === nr && ep.col === nc)
                moves.push({ fr: r, fc: c, tr: nr, tc: nc, enPassant: true });
            }
            break;
          }
          case KNIGHT: {
            for (const [dr, dc] of [[-2,-1],[-2,1],[-1,-2],[-1,2],[1,-2],[1,2],[2,-1],[2,1]]) {
              const nr = r+dr, nc = c+dc;
              if (!inBounds(nr, nc)) continue;
              if (!b[nr][nc] || b[nr][nc].color !== color)
                moves.push({ fr: r, fc: c, tr: nr, tc: nc });
            }
            break;
          }
          case BISHOP: addSliding(moves, b, r, c, color, [[-1,-1],[-1,1],[1,-1],[1,1]]); break;
          case ROOK:   addSliding(moves, b, r, c, color, [[-1,0],[1,0],[0,-1],[0,1]]); break;
          case QUEEN:  addSliding(moves, b, r, c, color, [[-1,-1],[-1,1],[1,-1],[1,1],[-1,0],[1,0],[0,-1],[0,1]]); break;
          case KING: {
            for (const [dr, dc] of [[-1,-1],[-1,0],[-1,1],[0,-1],[0,1],[1,-1],[1,0],[1,1]]) {
              const nr = r+dr, nc = c+dc;
              if (!inBounds(nr, nc)) continue;
              if (!b[nr][nc] || b[nr][nc].color !== color)
                moves.push({ fr: r, fc: c, tr: nr, tc: nc });
            }
            // Castling
            if (castle) {
              const row = color === W ? 7 : 0;
              if (r === row && c === 4) {
                // King side
                const ck = color === W ? castle.wk : castle.bk;
                if (ck && !b[row][5] && !b[row][6] && b[row][7] && b[row][7].type === ROOK && b[row][7].color === color) {
                  if (!isSquareAttacked(b, row, 4, color) && !isSquareAttacked(b, row, 5, color) && !isSquareAttacked(b, row, 6, color))
                    moves.push({ fr: row, fc: 4, tr: row, tc: 6, castle: 'k' });
                }
                // Queen side
                const cq = color === W ? castle.wq : castle.bq;
                if (cq && !b[row][3] && !b[row][2] && !b[row][1] && b[row][0] && b[row][0].type === ROOK && b[row][0].color === color) {
                  if (!isSquareAttacked(b, row, 4, color) && !isSquareAttacked(b, row, 3, color) && !isSquareAttacked(b, row, 2, color))
                    moves.push({ fr: row, fc: 4, tr: row, tc: 2, castle: 'q' });
                }
              }
            }
            break;
          }
        }
      }
    }
    return moves;
  }

  function addSliding(moves, b, r, c, color, dirs) {
    for (const [dr, dc] of dirs) {
      let nr = r + dr, nc = c + dc;
      while (inBounds(nr, nc)) {
        if (b[nr][nc]) {
          if (b[nr][nc].color !== color) moves.push({ fr: r, fc: c, tr: nr, tc: nc });
          break;
        }
        moves.push({ fr: r, fc: c, tr: nr, tc: nc });
        nr += dr; nc += dc;
      }
    }
  }

  function isSquareAttacked(b, row, col, byWhom) {
    // Check if square (row,col) is attacked by the OPPONENT of byWhom
    const opp = byWhom === W ? B : W;
    const oppDir = opp === W ? -1 : 1;

    // Pawn attacks
    for (const dc of [-1, 1]) {
      const pr = row - oppDir, pc2 = col + dc;
      if (inBounds(pr, pc2) && b[pr][pc2] && b[pr][pc2].color === opp && b[pr][pc2].type === PAWN)
        return true;
    }

    // Knight attacks
    for (const [dr, dc] of [[-2,-1],[-2,1],[-1,-2],[-1,2],[1,-2],[1,2],[2,-1],[2,1]]) {
      const nr = row+dr, nc = col+dc;
      if (inBounds(nr, nc) && b[nr][nc] && b[nr][nc].color === opp && b[nr][nc].type === KNIGHT)
        return true;
    }

    // Sliding: bishop/queen diagonals
    for (const [dr, dc] of [[-1,-1],[-1,1],[1,-1],[1,1]]) {
      let nr = row+dr, nc = col+dc;
      while (inBounds(nr, nc)) {
        if (b[nr][nc]) {
          if (b[nr][nc].color === opp && (b[nr][nc].type === BISHOP || b[nr][nc].type === QUEEN)) return true;
          break;
        }
        nr += dr; nc += dc;
      }
    }

    // Sliding: rook/queen straights
    for (const [dr, dc] of [[-1,0],[1,0],[0,-1],[0,1]]) {
      let nr = row+dr, nc = col+dc;
      while (inBounds(nr, nc)) {
        if (b[nr][nc]) {
          if (b[nr][nc].color === opp && (b[nr][nc].type === ROOK || b[nr][nc].type === QUEEN)) return true;
          break;
        }
        nr += dr; nc += dc;
      }
    }

    // King attacks
    for (const [dr, dc] of [[-1,-1],[-1,0],[-1,1],[0,-1],[0,1],[1,-1],[1,0],[1,1]]) {
      const nr = row+dr, nc = col+dc;
      if (inBounds(nr, nc) && b[nr][nc] && b[nr][nc].color === opp && b[nr][nc].type === KING)
        return true;
    }

    return false;
  }

  function findKing(b, color) {
    for (let r = 0; r < 8; r++)
      for (let c = 0; c < 8; c++)
        if (b[r][c] && b[r][c].color === color && b[r][c].type === KING)
          return { row: r, col: c };
    return null;
  }

  function inCheck(b, color) {
    const k = findKing(b, color);
    return k ? isSquareAttacked(b, k.row, k.col, color) : false;
  }

  function applyMoveOnBoard(b, m) {
    const nb = cloneBoard(b);
    const piece = nb[m.fr][m.fc];

    // En passant capture
    if (m.enPassant) {
      nb[m.fr][m.tc] = null; // remove captured pawn
    }

    // Castling – move rook
    if (m.castle) {
      const row = m.fr;
      if (m.castle === 'k') { nb[row][5] = nb[row][7]; nb[row][7] = null; }
      else                   { nb[row][3] = nb[row][0]; nb[row][0] = null; }
    }

    nb[m.tr][m.tc] = piece;
    nb[m.fr][m.fc] = null;

    // Promotion placeholder (caller handles actual promotion piece)
    if (m.promote) {
      nb[m.tr][m.tc] = { color: piece.color, type: m.promote };
    }

    return nb;
  }

  // Get legal moves for current side
  function legalMoves(color) {
    const raw = pseudoMoves(board, color, enPassantTarget, castleRights);
    const legal = [];
    for (const m of raw) {
      // Check if pawn reaches promotion rank
      const piece = board[m.fr][m.fc];
      if (piece.type === PAWN && (m.tr === 0 || m.tr === 7) && !m.promote) {
        // Generate a move for each promotion piece; legality checked per move
        for (const promo of [QUEEN, ROOK, BISHOP, KNIGHT]) {
          const pm = { ...m, promote: promo };
          const nb = applyMoveOnBoard(board, pm);
          if (!inCheck(nb, color)) legal.push(pm);
        }
        continue;
      }
      const nb = applyMoveOnBoard(board, m);
      if (!inCheck(nb, color)) legal.push(m);
    }
    return legal;
  }

  function getLegalForSquare(r, c) {
    if (!legalCache) legalCache = legalMoves(turn);
    return legalCache.filter(m => m.fr === r && m.fc === c);
  }

  // ── Execute move ──
  function executeMove(m) {
    // Save undo state
    history.push({
      board: cloneBoard(board),
      turn,
      castleRights: { ...castleRights },
      enPassantTarget: enPassantTarget ? { ...enPassantTarget } : null,
      halfMoveClock,
      lastMove,
      capturedWhite: [...capturedWhite],
      capturedBlack: [...capturedBlack],
      positionCounts: { ...positionCounts }
    });

    const piece = board[m.fr][m.fc];
    const captured = board[m.tr][m.tc];

    // Track captured pieces
    if (captured) {
      if (turn === W) capturedWhite.push(captured);
      else capturedBlack.push(captured);
    }
    if (m.enPassant) {
      const epPiece = board[m.fr][m.tc];
      if (turn === W) capturedWhite.push(epPiece);
      else capturedBlack.push(epPiece);
    }

    // Half-move clock
    if (piece.type === PAWN || captured || m.enPassant) halfMoveClock = 0;
    else halfMoveClock++;

    // En passant target
    if (piece.type === PAWN && Math.abs(m.tr - m.fr) === 2)
      enPassantTarget = { row: (m.fr + m.tr) / 2, col: m.fc };
    else
      enPassantTarget = null;

    // Castle rights
    if (piece.type === KING) {
      if (piece.color === W) { castleRights.wk = false; castleRights.wq = false; }
      else { castleRights.bk = false; castleRights.bq = false; }
    }
    if (piece.type === ROOK) {
      if (m.fr === 7 && m.fc === 7) castleRights.wk = false;
      if (m.fr === 7 && m.fc === 0) castleRights.wq = false;
      if (m.fr === 0 && m.fc === 7) castleRights.bk = false;
      if (m.fr === 0 && m.fc === 0) castleRights.bq = false;
    }
    // If a rook is captured
    if (captured && captured.type === ROOK) {
      if (m.tr === 7 && m.tc === 7) castleRights.wk = false;
      if (m.tr === 7 && m.tc === 0) castleRights.wq = false;
      if (m.tr === 0 && m.tc === 7) castleRights.bk = false;
      if (m.tr === 0 && m.tc === 0) castleRights.bq = false;
    }

    board = applyMoveOnBoard(board, m);
    lastMove = { fr: m.fr, fc: m.fc, tr: m.tr, tc: m.tc };
    turn = turn === W ? B : W;
    legalCache = null;

    recordPosition();
  }

  function undo() {
    if (history.length === 0) return;
    const s = history.pop();
    board = s.board;
    turn = s.turn;
    castleRights = s.castleRights;
    enPassantTarget = s.enPassantTarget;
    halfMoveClock = s.halfMoveClock;
    lastMove = s.lastMove;
    capturedWhite = s.capturedWhite;
    capturedBlack = s.capturedBlack;
    positionCounts = s.positionCounts;
    selected = null;
    legalCache = null;
    render();
  }

  // ── Game-over detection ──
  function gameStatus() {
    const moves = legalMoves(turn);
    if (moves.length === 0) {
      if (inCheck(board, turn)) return { over: true, result: 'checkmate', winner: turn === W ? B : W };
      return { over: true, result: 'stalemate' };
    }
    // 50-move rule
    if (halfMoveClock >= 100) return { over: true, result: 'draw', reason: '50-move rule' };
    // Threefold repetition
    const h = boardHash();
    if ((positionCounts[h] || 0) >= 3) return { over: true, result: 'draw', reason: 'threefold repetition' };
    // Insufficient material
    if (insufficientMaterial()) return { over: true, result: 'draw', reason: 'insufficient material' };
    return { over: false };
  }

  function insufficientMaterial() {
    const pieces = { w: [], b: [] };
    for (let r = 0; r < 8; r++)
      for (let c = 0; c < 8; c++) {
        const p = board[r][c];
        if (p) pieces[p.color].push(p.type);
      }
    // K vs K
    if (pieces.w.length === 1 && pieces.b.length === 1) return true;
    // K+B vs K or K+N vs K
    if (pieces.w.length === 1 && pieces.b.length === 2 && (pieces.b.includes(BISHOP) || pieces.b.includes(KNIGHT))) return true;
    if (pieces.b.length === 1 && pieces.w.length === 2 && (pieces.w.includes(BISHOP) || pieces.w.includes(KNIGHT))) return true;
    return false;
  }

  // ── Rendering ──
  const boardEl = document.getElementById('board');
  const turnEl = document.getElementById('turn-indicator');
  const capturedByWhiteEl = document.getElementById('captured-by-white');
  const capturedByBlackEl = document.getElementById('captured-by-black');
  const playerWhiteEl = document.getElementById('player-white');
  const playerBlackEl = document.getElementById('player-black');
  const promotionModal = document.getElementById('promotion-modal');
  const promotionChoices = document.getElementById('promotion-choices');
  const gameOverModal = document.getElementById('game-over-modal');
  const gameOverTitle = document.getElementById('game-over-title');
  const gameOverMessage = document.getElementById('game-over-message');

  const FILES = 'abcdefgh';

  function render() {
    boardEl.innerHTML = '';
    const movesForSelected = selected ? getLegalForSquare(selected.row, selected.col) : [];
    const legalTargets = new Map();
    for (const m of movesForSelected) {
      legalTargets.set(m.tr * 8 + m.tc, m);
    }

    const checkKing = inCheck(board, turn) ? findKing(board, turn) : null;

    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const sq = document.createElement('div');
        const isLight = (r + c) % 2 === 0;
        sq.className = 'square ' + (isLight ? 'light' : 'dark');

        // Highlight last move
        if (lastMove && ((r === lastMove.fr && c === lastMove.fc) || (r === lastMove.tr && c === lastMove.tc))) {
          sq.classList.add('last-move');
        }

        // Highlight selected
        if (selected && selected.row === r && selected.col === c) {
          sq.classList.add('selected');
        }

        // Check highlight
        if (checkKing && checkKing.row === r && checkKing.col === c) {
          sq.classList.add('check');
        }

        // Legal move indicators
        const key = r * 8 + c;
        if (legalTargets.has(key)) {
          if (board[r][c] || (legalTargets.get(key).enPassant)) {
            sq.classList.add('legal-capture');
          } else {
            sq.classList.add('legal-move');
          }
        }

        // Piece
        const p = board[r][c];
        if (p) {
          const span = document.createElement('span');
          span.className = 'piece';
          span.textContent = GLYPHS[p.color + p.type];
          sq.appendChild(span);
        }

        // Rank labels (left column)
        if (c === 0) {
          const label = document.createElement('span');
          label.className = 'rank-label';
          label.textContent = 8 - r;
          sq.appendChild(label);
        }
        // File labels (bottom row)
        if (r === 7) {
          const label = document.createElement('span');
          label.className = 'file-label';
          label.textContent = FILES[c];
          sq.appendChild(label);
        }

        sq.addEventListener('click', () => onSquareClick(r, c));
        boardEl.appendChild(sq);
      }
    }

    // Turn indicator
    turnEl.textContent = (turn === W ? "White" : "Black") + "'s turn";
    playerWhiteEl.classList.toggle('active', turn === W);
    playerBlackEl.classList.toggle('active', turn === B);

    // Captured pieces
    renderCaptured(capturedByWhiteEl, capturedWhite);
    renderCaptured(capturedByBlackEl, capturedBlack);

    // Check game-over
    const status = gameStatus();
    if (status.over) {
      showGameOver(status);
    }
  }

  function renderCaptured(el, pieces) {
    // Sort by value descending
    const sorted = [...pieces].sort((a, b2) => PIECE_VALUE[b2.type] - PIECE_VALUE[a.type]);
    el.textContent = sorted.map(p => GLYPHS[p.color + p.type]).join('');
  }

  // ── Interaction ──
  let pendingPromotion = null;

  function onSquareClick(r, c) {
    if (pendingPromotion) return;

    const piece = board[r][c];

    if (selected) {
      const moves = getLegalForSquare(selected.row, selected.col);
      const target = moves.filter(m => m.tr === r && m.tc === c);

      if (target.length > 0) {
        // If it's a promotion move, multiple options exist
        const promoMoves = target.filter(m => m.promote);
        if (promoMoves.length > 0) {
          showPromotionDialog(promoMoves);
          return;
        }
        executeMove(target[0]);
        selected = null;
        render();
        return;
      }

      // Click on own piece — reselect
      if (piece && piece.color === turn) {
        selected = { row: r, col: c };
        render();
        return;
      }

      // Click elsewhere — deselect
      selected = null;
      render();
      return;
    }

    // No selection yet — select own piece
    if (piece && piece.color === turn) {
      selected = { row: r, col: c };
      render();
    }
  }

  function showPromotionDialog(moves) {
    pendingPromotion = moves;
    promotionChoices.innerHTML = '';
    const color = turn;
    for (const promo of [QUEEN, ROOK, BISHOP, KNIGHT]) {
      const btn = document.createElement('button');
      btn.textContent = GLYPHS[color + promo];
      btn.addEventListener('click', () => {
        const m = moves.find(mv => mv.promote === promo);
        executeMove(m);
        selected = null;
        pendingPromotion = null;
        promotionModal.classList.add('hidden');
        render();
      });
      promotionChoices.appendChild(btn);
    }
    promotionModal.classList.remove('hidden');
  }

  function showGameOver(status) {
    let title, msg;
    if (status.result === 'checkmate') {
      const winner = status.winner === W ? 'White' : 'Black';
      title = 'Checkmate!';
      msg = winner + ' wins the game.';
    } else if (status.result === 'stalemate') {
      title = 'Stalemate!';
      msg = 'The game is a draw.';
    } else {
      title = 'Draw!';
      msg = status.reason ? ('Draw by ' + status.reason + '.') : 'The game is a draw.';
    }
    gameOverTitle.textContent = title;
    gameOverMessage.textContent = msg;
    gameOverModal.classList.remove('hidden');
  }

  // ── Buttons ──
  document.getElementById('reset-btn').addEventListener('click', () => {
    gameOverModal.classList.add('hidden');
    promotionModal.classList.add('hidden');
    pendingPromotion = null;
    initGame();
  });

  document.getElementById('undo-btn').addEventListener('click', () => {
    gameOverModal.classList.add('hidden');
    undo();
  });

  document.getElementById('new-game-btn').addEventListener('click', () => {
    gameOverModal.classList.add('hidden');
    promotionModal.classList.add('hidden');
    pendingPromotion = null;
    initGame();
  });

  // ── Start ──
  initGame();
})();
