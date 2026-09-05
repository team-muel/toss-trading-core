"""Sample, EWMA, shrinkage and stress covariance estimators."""
from decimal import Decimal, localcontext
from asset_management.domain.errors import DataQualityError
from .models import CovarianceEstimate, ReturnPanel

def _means(rows): return tuple(sum(row[j] for row in rows)/Decimal(len(rows)) for j in range(len(rows[0])))
def sample_covariance(panel: ReturnPanel) -> CovarianceEstimate:
    rows=panel.returns; n=len(rows); means=_means(rows); k=len(means)
    if n<2: raise DataQualityError("COVARIANCE_HISTORY_INSUFFICIENT")
    matrix=tuple(tuple(sum((row[i]-means[i])*(row[j]-means[j]) for row in rows)/Decimal(n-1)*panel.periods_per_year
                       for j in range(k)) for i in range(k))
    return _estimate(matrix,"SAMPLE",n,annualization=panel.periods_per_year)

def ewma_covariance(panel: ReturnPanel, decay: Decimal=Decimal("0.94")) -> CovarianceEstimate:
    if not Decimal(0)<decay<Decimal(1): raise DataQualityError("EWMA_DECAY_INVALID")
    rows=panel.returns; k=len(rows[0])
    raw=[(1-decay)*decay**(len(rows)-1-t) for t in range(len(rows))]; total=sum(raw)
    weights=[x/total for x in raw]
    means=tuple(sum(weights[t]*rows[t][j] for t in range(len(rows))) for j in range(k))
    matrix=tuple(tuple(sum(weights[t]*(rows[t][i]-means[i])*(rows[t][j]-means[j]) for t in range(len(rows)))*panel.periods_per_year
                       for j in range(k)) for i in range(k))
    return _estimate(matrix,"EWMA",len(rows),annualization=panel.periods_per_year)

def shrink_covariance(sample: CovarianceEstimate, target: tuple[tuple[Decimal,...],...], alpha: Decimal) -> CovarianceEstimate:
    if not Decimal(0)<=alpha<=Decimal(1): raise DataQualityError("SHRINKAGE_ALPHA_INVALID")
    n=len(sample.matrix)
    if len(target)!=n or any(len(row)!=n for row in target): raise DataQualityError("COVARIANCE_DIMENSION_INVALID")
    matrix=tuple(tuple(alpha*target[i][j]+(1-alpha)*sample.matrix[i][j] for j in range(n)) for i in range(n))
    return _estimate(matrix,"SHRINKAGE",sample.observation_count,annualization=sample.annualization_factor)

def factor_covariance(loadings, factor_matrix, idiosyncratic_variance) -> CovarianceEstimate:
    n=len(loadings); factors=len(factor_matrix)
    if (not n or len(idiosyncratic_variance)!=n or any(len(row)!=factors for row in factor_matrix)
            or any(len(row)!=factors for row in loadings)):
        raise DataQualityError("FACTOR_COVARIANCE_DIMENSION_INVALID")
    matrix=tuple(tuple(sum(loadings[i][a]*factor_matrix[a][b]*loadings[j][b]
                           for a in range(factors) for b in range(factors))+(idiosyncratic_variance[i] if i==j else 0)
                       for j in range(n)) for i in range(n))
    return _estimate(matrix,"FACTOR",0)

def stress_covariance(normal: CovarianceEstimate, volatility_multiplier: Decimal,
                      correlation_floor: Decimal) -> CovarianceEstimate:
    if volatility_multiplier<1 or not Decimal(-1)<=correlation_floor<=Decimal(1):
        raise DataQualityError("STRESS_COVARIANCE_POLICY_INVALID")
    n=len(normal.matrix); variances=[normal.matrix[i][i]*volatility_multiplier**2 for i in range(n)]
    matrix=[]
    for i in range(n):
        row=[]
        for j in range(n):
            if i==j: row.append(variances[i]); continue
            denom=(normal.matrix[i][i]*normal.matrix[j][j]).sqrt()
            corr=normal.matrix[i][j]/denom if denom else Decimal(0)
            corr=max(corr,correlation_floor)
            row.append(corr*(variances[i]*variances[j]).sqrt())
        matrix.append(tuple(row))
    return _estimate(tuple(matrix),"STRESS",normal.observation_count,True,normal.annualization_factor)

def is_psd(matrix, tolerance=Decimal("1e-18")):
    n=len(matrix); lower=[[Decimal(0)]*n for _ in range(n)]
    for i in range(n):
        for j in range(i+1):
            residual=matrix[i][j]-sum(lower[i][k]*lower[j][k] for k in range(j))
            if i==j:
                if residual < -tolerance: return False
                lower[i][j]=max(Decimal(0),residual).sqrt()
            elif lower[j][j]>tolerance: lower[i][j]=residual/lower[j][j]
            elif abs(residual)>tolerance: return False
    return True

def _estimate(matrix,method,count,stressed=False,annualization=1):
    if not is_psd(matrix): raise DataQualityError("COVARIANCE_NOT_PSD")
    return CovarianceEstimate(tuple(tuple(x for x in row) for row in matrix),method,count,True,stressed,annualization)

def safe_inverse(estimate: CovarianceEstimate, tolerance: Decimal=Decimal("1e-12")):
    matrix=estimate.matrix; n=len(matrix); work=[list(row)+[Decimal(i==j) for j in range(n)] for i,row in enumerate(matrix)]
    try:
        for col in range(n):
            pivot=max(range(col,n),key=lambda r:abs(work[r][col]))
            if abs(work[pivot][col])<=tolerance: raise ArithmeticError
            work[col],work[pivot]=work[pivot],work[col]; divisor=work[col][col]
            work[col]=[x/divisor for x in work[col]]
            for row in range(n):
                if row!=col:
                    factor=work[row][col]; work[row]=[work[row][j]-factor*work[col][j] for j in range(2*n)]
        return tuple(tuple(row[n:]) for row in work),False
    except ArithmeticError:
        diagonal=tuple(tuple((1/matrix[i][i] if i==j and matrix[i][i]>tolerance else Decimal(0)) for j in range(n)) for i in range(n))
        return diagonal,True
