import type { Grade, Position, WorkType } from "@shared/model/domain";

export type WorkTypeMatrix = Record<string, string[]>;

export function createDefaultMatrix(positions: Position[], grades: Grade[]): WorkTypeMatrix {
  return positions.reduce<WorkTypeMatrix>((matrix, position) => {
    const gradeIds = position.gradeIds.length > 0 ? position.gradeIds : grades.map((grade) => grade.id);

    matrix[position.id] = gradeIds;
    return matrix;
  }, {});
}

export function hydrateWorkTypeMatrix(
  workType: Pick<WorkType, "allowedGradeIds" | "allowedPositionIds" | "positionGradeMatrix">,
  positions: Position[],
): WorkTypeMatrix {
  if (workType.positionGradeMatrix) {
    return positions.reduce<WorkTypeMatrix>((matrix, position) => {
      matrix[position.id] = workType.positionGradeMatrix?.[position.id] ?? [];
      return matrix;
    }, {});
  }

  const allowedGrades = new Set(workType.allowedGradeIds);
  const restrictedPositions = new Set(workType.allowedPositionIds);
  const hasPositionRestriction = workType.allowedPositionIds.length > 0;

  return positions.reduce<WorkTypeMatrix>((matrix, position) => {
    if (hasPositionRestriction && !restrictedPositions.has(position.id)) {
      matrix[position.id] = [];
      return matrix;
    }

    matrix[position.id] = position.gradeIds.filter((gradeId) => allowedGrades.has(gradeId));
    return matrix;
  }, {});
}

export function toggleMatrixGrade(matrix: WorkTypeMatrix, positionId: string, gradeId: string) {
  const currentGradeIds = matrix[positionId] ?? [];
  const nextGradeIds = currentGradeIds.includes(gradeId)
    ? currentGradeIds.filter((id) => id !== gradeId)
    : [...currentGradeIds, gradeId];

  return {
    ...matrix,
    [positionId]: nextGradeIds,
  };
}

export function toWorkTypeAccessPayload(matrix: WorkTypeMatrix) {
  const selectedEntries = Object.entries(matrix).filter(([, gradeIds]) => gradeIds.length > 0);
  const allowedGradeIds = Array.from(new Set(selectedEntries.flatMap(([, gradeIds]) => gradeIds)));
  const allowedPositionIds = selectedEntries.map(([positionId]) => positionId);

  return {
    allowedGradeIds,
    allowedPositionIds,
    positionGradeMatrix: Object.fromEntries(selectedEntries),
  };
}

export function getMatrixSelectionCount(matrix: WorkTypeMatrix) {
  return Object.values(matrix).reduce((count, gradeIds) => count + gradeIds.length, 0);
}

export function getPositionGradeSummary(matrix: WorkTypeMatrix, positions: Position[], grades: Grade[]) {
  const gradeNameById = new Map(grades.map((grade) => [grade.id, grade.name]));
  const selectedPositions = positions
    .map((position) => {
      const gradeNames = (matrix[position.id] ?? [])
        .map((gradeId) => gradeNameById.get(gradeId) ?? gradeId);

      return gradeNames.length > 0 ? `${position.name}: ${gradeNames.join(", ")}` : "";
    })
    .filter(Boolean);

  return selectedPositions.length > 0 ? selectedPositions.join("; ") : "Нет выбранных должностей и грейдов";
}
